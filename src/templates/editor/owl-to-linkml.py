#!/usr/bin/env python3
"""
onto_to_linkml_schema.py — Generate a robot-annotated LinkML schema.yaml
                            from an OWL ontology.

Two operating modes
-------------------

OWL-driven mode  (--class required, --spec not needed):
  Derives all schema structure directly from OWL restrictions and annotations.
  Every object-property restriction found across the subclass hierarchy becomes
  a slot; standard annotation slots (ID, label, parent, definition, …) are
  always prepended automatically.

  python onto_to_linkml_schema.py \\
      --ontology swo-edit.owl \\
      --class    http://www.ebi.ac.uk/swo/SWO_0000001 \\
      --output   schema.yaml

  Optional flags (OWL-driven mode):
    --prefixes  prefixes.yaml      prefix→namespace map; auto-discovered if omitted
    --config    schema_config.yaml persist enum accept/narrow/skip decisions
    --schema-id    IRI             override auto-generated schema @id
    --schema-name  STR             override auto-generated schema name
    --schema-title STR             override auto-generated schema title
    --schema-version STR           override auto-generated schema version
    --default-prefix STR           override auto-detected default prefix
    --rarity-threshold FLOAT       0–1 fraction; properties used by fewer
                                   subclasses prompt interactively (default 0.10)

Spec-driven mode  (--spec required, legacy):
  Reads schema structure from a hand-authored YAML spec file.

  python onto_to_linkml_schema.py \\
      --spec   software_schema_spec.yaml \\
      --output schema.yaml

  --ontology may override the ontology path named in the spec.

Each slot's robot annotation is written as:
    annotations:
      robot: <ROBOT template code>

Supported enum query types (spec enums.<name>.query.type):
  subclasses_of          root_iri, include_root (bool, default true)
  range_of_property      property_iri, ns_fallbacks (list of namespace strings)
  individuals_in_namespace  namespace
  roots_union            root_iris (list); union of subclasses of each root
                         (produced by interactive narrowing)
"""
import argparse
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required.  pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from rdflib import BNode, Graph, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS


# ---------------------------------------------------------------------------
# Well-known namespace → canonical prefix table
# ---------------------------------------------------------------------------

WELL_KNOWN_PREFIXES = {
    "https://w3id.org/linkml/":                   "linkml",
    "http://www.w3.org/2001/XMLSchema#":           "xsd",
    "http://www.w3.org/2000/01/rdf-schema#":       "rdfs",
    "http://www.w3.org/2002/07/owl#":              "owl",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://purl.org/dc/terms/":                   "dcterms",
    "http://purl.org/dc/elements/1.1/":            "dc",
    "http://purl.obolibrary.org/obo/IAO_":         "IAO",
    "http://purl.obolibrary.org/obo/RO_":          "RO",
    "http://purl.obolibrary.org/obo/OBI_":         "OBI",
    "http://purl.obolibrary.org/obo/BFO_":         "BFO",
    "http://edamontology.org/":                    "EDAM",
}

# ---------------------------------------------------------------------------
# Standard annotation slots — always generated first in OWL-driven mode.
# The 'parent' slot's range is filled in dynamically by build_schema_from_owl.
# ---------------------------------------------------------------------------

_STANDARD_SLOTS = [
    {
        "name": "Ontology ID",
        "slot_uri": "dcterms:identifier",
        "range": "WhitespaceMinimizedString",
        "identifier": True,
        "unique": True,
        "required": True,
        "slot_group": "Ontology Metadata",
        "robot": "ID",
        "description": (
            "CURIE identifier for this class (e.g. SWO:0000005). "
            "For new entries use the next available numeric ID."
        ),
    },
    {
        "name": "label",
        "slot_uri": "rdfs:label",
        "range": "WhitespaceMinimizedString",
        "required": True,
        "slot_group": "Ontology Metadata",
        "robot": "AL rdfs:label@en",
        "description": "Primary English name (rdfs:label) of this class.",
    },
    {
        "name": "parent",
        "range": None,          # filled in by build_schema_from_owl
        "slot_group": "Ontology Metadata",
        "robot": "SC %",
        "description": "Direct OWL superclass of this entry.",
    },
    {
        "name": "definition",
        "slot_uri": "IAO:0000115",
        "range": "string",
        "slot_group": "Ontology Metadata",
        "robot": "AL IAO:0000115@en",
        "description": "Textual definition (IAO:0000115) for this class.",
    },
    {
        "name": "definition source",
        "slot_uri": "IAO:0000119",
        "range": "WhitespaceMinimizedString",
        "multivalued": True,
        "slot_group": "Ontology Metadata",
        "robot": "A IAO:0000119 SPLIT=|",
        "description": (
            "Source URI(s) for the definition (IAO:0000119). "
            "Separate multiple values with |."
        ),
    },
    {
        "name": "comment",
        "slot_uri": "rdfs:comment",
        "range": "string",
        "slot_group": "Ontology Metadata",
        "robot": "AL rdfs:comment@en",
        "description": "Free-text rdfs:comment annotation.",
    },
    {
        "name": "term creator",
        "slot_uri": "dcterms:creator",
        "range": "WhitespaceMinimizedString",
        "multivalued": True,
        "slot_group": "Ontology Metadata",
        "robot": "A dcterms:creator SPLIT=|",
        "description": (
            "Name(s) of the curator(s) who added this entry (dcterms:creator). "
            "Separate multiple values with |."
        ),
    },
]


# ---------------------------------------------------------------------------
# OWL helpers
# ---------------------------------------------------------------------------

def all_subclasses(g: Graph, root: URIRef) -> set:
    """Return all transitive subclasses of *root* (not including root itself)."""
    seen: set = set()
    queue = [s for s in g.subjects(RDFS.subClassOf, root) if isinstance(s, URIRef)]
    while queue:
        cls = queue.pop()
        if cls in seen:
            continue
        seen.add(cls)
        queue.extend(
            s for s in g.subjects(RDFS.subClassOf, cls) if isinstance(s, URIRef)
        )
    return seen


def is_deprecated(g: Graph, uri: URIRef) -> bool:
    """Return True if *uri* carries owl:deprecated true."""
    return bool(g.value(uri, OWL.deprecated))


def classes_to_lookup_rows(
    g: Graph,
    cls_set: set,
    prefix_map: dict,
    root_iris: frozenset = frozenset(),
) -> list:
    """
    Given a set of class URIRefs, return sorted [(curie, label, iri, parent_curie)]
    rows.  Skips deprecated terms and classes without a label or a mapped CURIE.

    parent_curie is set when a class has a named rdfs:subClassOf parent that is
    also in cls_set AND is not one of root_iris.  This encodes is_a hierarchy
    for entries that are deeper than one level from the enum root(s).
    """
    # Pre-compute {URIRef → curie} for valid (non-deprecated, labeled, mapped) members
    cls_to_curie: dict = {}
    for cls in cls_set:
        if not isinstance(cls, URIRef):
            continue
        if is_deprecated(g, cls):
            continue
        if not g.value(cls, RDFS.label):
            continue
        c = iri_to_curie(str(cls), prefix_map)
        if not c.startswith("<"):
            cls_to_curie[cls] = c

    items = []
    for cls, curie in cls_to_curie.items():
        label = str(g.value(cls, RDFS.label))

        # Find the nearest named parent that is also in the enum but not a root.
        # (Direct children of a root get no is_a — they're at level 1.)
        parent_curie = None
        for parent in g.objects(cls, RDFS.subClassOf):
            if (isinstance(parent, URIRef)
                    and parent in cls_to_curie
                    and parent not in root_iris):
                parent_curie = cls_to_curie[parent]
                break

        items.append((curie, label, str(cls), parent_curie))
    return sorted(items, key=lambda x: x[1].casefold())


def individuals_in_namespace(g: Graph, namespace: str, prefix_map: dict) -> list:
    """Return sorted [(curie, label, iri, None)] for non-deprecated
    NamedIndividuals whose IRI starts with *namespace*.
    (Individuals have no class hierarchy, so parent_curie is always None.)"""
    items = []
    for ind in g.subjects(RDF.type, OWL.NamedIndividual):
        if not (isinstance(ind, URIRef) and str(ind).startswith(namespace)):
            continue
        if is_deprecated(g, ind):
            continue
        label = g.value(ind, RDFS.label)
        if not label:
            continue
        curie = iri_to_curie(str(ind), prefix_map)
        if curie.startswith("<"):
            continue
        items.append((curie, str(label), str(ind), None))
    return sorted(items, key=lambda x: x[1].casefold())


def classes_for_property(g: Graph, prop_iri: str, ns_fallbacks: list) -> set:
    """
    Return the set of URIRef classes that are valid fillers for *prop_iri*.

    Strategy:
      1. Collect rdfs:range declarations on the property and all their
         transitive subclasses.
      2. Add every OWL class found in each *ns_fallbacks* namespace (catches
         classes outside the declared range), then recurse into their
         transitive subclasses too.
    """
    prop  = URIRef(prop_iri)
    roots = {o for o in g.objects(prop, RDFS.range) if isinstance(o, URIRef)}

    result: set = set()
    for root in roots:
        result.add(root)
        result.update(all_subclasses(g, root))

    fallback_new: set = set()
    for ns in ns_fallbacks:
        for s in g.subjects(RDF.type, OWL.Class):
            if isinstance(s, URIRef) and str(s).startswith(ns) and s not in result:
                result.add(s)
                fallback_new.add(s)
    for s in fallback_new:
        result.update(all_subclasses(g, s))

    return result


# ---------------------------------------------------------------------------
# CURIE utilities
# ---------------------------------------------------------------------------

def iri_to_curie(iri_str: str, prefix_map: dict) -> str:
    """
    Convert a full IRI to a CURIE using *prefix_map* (prefix → namespace).
    Falls back to angle-bracket IRI syntax (<IRI>) if no prefix matches.
    prefix_map keys must NOT include a trailing colon.
    Sorts by namespace length descending so more specific prefixes match first.
    """
    for prefix, ns in sorted(prefix_map.items(), key=lambda x: len(x[1]), reverse=True):
        if iri_str.startswith(ns):
            return f"{prefix}:{iri_str[len(ns):]}"
    return f"<{iri_str}>"


# ---------------------------------------------------------------------------
# Enum building — dispatches to the right OWL query based on query type
# ---------------------------------------------------------------------------

def build_enum_items(g: Graph, query_spec: dict, prefix_map: dict) -> list:
    """
    Build sorted [(curie, label, iri, parent_curie)] rows for one enum from
    *query_spec*.  parent_curie encodes the is_a hierarchy for entries deeper
    than one level from the enum root(s).

    Supported query types:
      subclasses_of          root_iri, include_root (bool, default true)
      range_of_property      property_iri, ns_fallbacks (list)
      individuals_in_namespace  namespace
      roots_union            root_iris (list); union of subclasses of each root
    """
    qtype = query_spec.get("type")

    if qtype == "subclasses_of":
        root    = URIRef(query_spec["root_iri"])
        cls_set = all_subclasses(g, root)
        if query_spec.get("include_root", True):
            cls_set = cls_set | {root}
        return classes_to_lookup_rows(g, cls_set, prefix_map,
                                      root_iris=frozenset([root]))

    elif qtype == "range_of_property":
        cls_set = classes_for_property(
            g,
            query_spec["property_iri"],
            query_spec.get("ns_fallbacks", []),
        )
        # Treat the property's declared rdfs:range class(es) as roots so that
        # direct children of those ranges don't get a redundant is_a.
        prop = URIRef(query_spec["property_iri"])
        range_roots = frozenset(
            o for o in g.objects(prop, RDFS.range) if isinstance(o, URIRef)
        )
        return classes_to_lookup_rows(g, cls_set, prefix_map,
                                      root_iris=range_roots)

    elif qtype == "individuals_in_namespace":
        return individuals_in_namespace(g, query_spec["namespace"], prefix_map)

    elif qtype == "roots_union":
        result: set = set()
        for root_iri_str in query_spec.get("root_iris", []):
            r = URIRef(root_iri_str)
            result.add(r)
            result.update(all_subclasses(g, r))
        roots = frozenset(URIRef(r) for r in query_spec.get("root_iris", []))
        return classes_to_lookup_rows(g, result, prefix_map, root_iris=roots)

    else:
        print(f"WARNING: unknown enum query type {qtype!r} — skipping", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Schema config I/O
# ---------------------------------------------------------------------------

def load_schema_config(path: str) -> dict:
    """Load schema_config.yaml; return {} if file does not exist."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_schema_config(path: str, config: dict) -> None:
    """Write config dict to schema_config.yaml with a header comment."""
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    header = (
        "# Schema configuration for onto_to_linkml_schema.py\n"
        "# enums.<name>.action: accept | narrow | skip\n"
        "# excluded_properties: property IRIs skipped by rarity prompt\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=True)
    print(f"Config saved to {path}")


# ---------------------------------------------------------------------------
# Snake-case helper
# ---------------------------------------------------------------------------

def to_snake(name: str) -> str:
    """Convert a human-readable slot name to snake_case.
    Strips parenthetical qualifiers and replaces non-alphanumeric runs
    with underscores."""
    name = re.sub(r"\s*\([^)]*\)", "", name)    # drop "(IAO data item)" etc.
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)  # non-alnum runs → _
    return name.strip("_").lower()


# ---------------------------------------------------------------------------
# Namespace discovery (OWL-driven mode)
# ---------------------------------------------------------------------------

def parse_owl_namespaces(owl_path: str) -> dict:
    """Read xmlns: declarations from the root element of an OWL/RDF-XML file.
    Returns {prefix: namespace_uri} without trailing colons on keys."""
    result = {}
    with open(owl_path, "rb") as fh:
        header = fh.read(8192).decode("utf-8", errors="replace")
    for m in re.finditer(r'xmlns:([A-Za-z_][A-Za-z0-9_.-]*)="([^"]+)"', header):
        result[m.group(1)] = m.group(2)
    return result


def _extract_ns(iri: str) -> str:
    """Extract namespace from an IRI using the numeric-segment heuristic.
    Returns the IRI up to and including the last separator before an all-digit
    local part (e.g. 'http://…/license/SWO_1000006' → 'http://…/license/SWO_').
    Returns '' if the heuristic does not match."""
    m = re.match(r'^(.*[/_])(\d+)$', iri)
    return m.group(1) if m else ""


def build_prefix_map_from_owl(owl_path: str, g: Graph) -> dict:
    """Build a prefix→namespace map by combining:
      1. Well-known prefix table
      2. XML-declared xmlns: prefixes from the OWL file
      3. Sub-namespace discovery via the numeric-segment heuristic

    Prints a WARNING for every auto-generated prefix name so users can verify
    or supply a --prefixes file on the next run.
    Returns the map (longer namespaces sort first in iri_to_curie)."""

    # --- 1. Seed from WELL_KNOWN_PREFIXES ---
    result: dict = {pfx: ns for ns, pfx in WELL_KNOWN_PREFIXES.items()}
    ns_to_prefix: dict = {ns: pfx for pfx, ns in result.items()}

    # --- 2. Merge XML-declared xmlns: prefixes ---
    for pfx, ns in parse_owl_namespaces(owl_path).items():
        if ns in ns_to_prefix:
            continue            # already mapped to a canonical prefix
        if pfx in result:
            continue            # prefix already claimed
        result[pfx] = ns
        ns_to_prefix[ns] = pfx

    # --- 3. Collect all named IRIs in the graph ---
    all_iris: set = set()
    for triple in g:
        for node in triple:
            if isinstance(node, URIRef):
                all_iris.add(str(node))

    # --- 4. Numeric-segment heuristic for uncovered sub-namespaces ---
    new_ns: set = set()
    for iri in all_iris:
        if any(iri.startswith(ns) for ns in result.values()):
            continue
        ns_candidate = _extract_ns(iri)
        if ns_candidate and ns_candidate not in result.values():
            new_ns.add(ns_candidate)

    # --- 5. Generate prefix names for newly discovered sub-namespaces ---
    used_prefixes: set = set(result.keys())
    for ns_candidate in sorted(new_ns):
        # Find longest matching declared namespace (parent)
        parent_prefix = ""
        parent_ns = ""
        for pfx, ns in result.items():
            if ns_candidate.startswith(ns) and len(ns) > len(parent_ns):
                parent_ns = ns
                parent_prefix = pfx

        if parent_prefix:
            sub = ns_candidate[len(parent_ns):]
            # First letter of first path component
            first_component = sub.split("/")[0] if "/" in sub else sub.rstrip("_")
            letters = re.sub(r'[^a-zA-Z]', '', first_component)
            suffix = letters[0].upper() if letters else "X"
            base_candidate = parent_prefix.upper() + suffix
        else:
            seg = ns_candidate.rstrip("/").rstrip("_").rsplit("/", 1)[-1]
            base_candidate = re.sub(r'[^A-Za-z0-9]', '', seg).upper()[:6] or "NS"

        # Ensure uniqueness
        candidate = base_candidate
        i = 1
        while candidate in used_prefixes:
            candidate = f"{base_candidate}{i}"
            i += 1

        result[candidate] = ns_candidate
        used_prefixes.add(candidate)
        print(
            f"WARNING: namespace {ns_candidate!r} not in known prefixes; "
            f"assigned prefix {candidate!r}. "
            f"Edit schema.yaml or use --prefixes if incorrect.",
            file=sys.stderr,
        )

    return result


# ---------------------------------------------------------------------------
# Ontology metadata
# ---------------------------------------------------------------------------

def get_ontology_metadata(g: Graph) -> dict:
    """Query the owl:Ontology node for dcterms:title, dcterms:description,
    owl:versionInfo.  Returns {title, description, version} with None values
    for missing fields."""
    DCTERMS = "http://purl.org/dc/terms/"
    ont = None
    for s in g.subjects(RDF.type, OWL.Ontology):
        ont = s
        break
    if ont is None:
        return {"title": None, "description": None, "version": None}
    return {
        "title":       str(t) if (t := g.value(ont, URIRef(DCTERMS + "title"))) else None,
        "description": str(d) if (d := g.value(ont, URIRef(DCTERMS + "description"))) else None,
        "version":     str(v) if (v := g.value(ont, OWL.versionInfo)) else None,
    }


# ---------------------------------------------------------------------------
# Class-expression parsing
# ---------------------------------------------------------------------------

def parse_ce(g: Graph, node) -> dict:
    """Recursively parse an OWL class expression node.

    Returns one of:
      {type:"class", iri:str}
      {type:"some",  property:str, filler:dict}
      {type:"value", property:str, value:str}
      {type:"and",   operands:[dict,...]}
      None  — unknown / unsupported

    owl:allValuesFrom is treated as owl:someValuesFrom: the cardinality
    constraint is irrelevant for ROBOT template object-property links.
    """
    if isinstance(node, URIRef):
        return {"type": "class", "iri": str(node)}

    if not isinstance(node, BNode):
        return None

    # owl:Restriction
    if (node, RDF.type, OWL.Restriction) in g:
        prop = g.value(node, OWL.onProperty)
        if prop is None:
            return None
        prop_iri = str(prop)

        filler = g.value(node, OWL.someValuesFrom)
        if filler is not None:
            return {"type": "some", "property": prop_iri, "filler": parse_ce(g, filler)}

        val = g.value(node, OWL.hasValue)
        if val is not None:
            return {"type": "value", "property": prop_iri, "value": str(val)}

        all_f = g.value(node, OWL.allValuesFrom)
        if all_f is not None:
            # Treat allValuesFrom as someValuesFrom — the cardinality constraint
            # is not expressible in a ROBOT template column but the property link
            # still needs a slot.
            return {"type": "some", "property": prop_iri, "filler": parse_ce(g, all_f)}

        return None

    # owl:intersectionOf
    list_node = g.value(node, OWL.intersectionOf)
    if list_node is not None:
        operands = []
        try:
            for item in Collection(g, list_node):
                ce = parse_ce(g, item)
                if ce is not None:
                    operands.append(ce)
        except Exception:
            return None
        return {"type": "and", "operands": operands}

    return None


def _ce_fingerprint(ce: dict) -> str:
    """Return a canonical string key for a CE dict (for deduplication)."""
    if ce is None:
        return "None"
    t = ce.get("type")
    if t == "class":
        return f"class:{ce['iri']}"
    if t == "some":
        return f"some:{ce['property']}:{_ce_fingerprint(ce.get('filler'))}"
    if t == "value":
        return f"value:{ce['property']}"
    if t == "all":
        return f"all:{ce['property']}:{_ce_fingerprint(ce.get('filler'))}"
    if t == "and":
        ops = sorted(_ce_fingerprint(op) for op in ce.get("operands", []))
        return f"and:({'|'.join(ops)})"
    return str(ce)


def _ce_to_robot(ce: dict, prefix_map: dict) -> str:
    """Convert a parsed CE dict to a ROBOT template code string.
    Returns None for unsupported patterns (complex intersections)."""
    if ce is None:
        return None
    t = ce.get("type")

    if t == "some":
        p = iri_to_curie(ce["property"], prefix_map)
        filler = ce.get("filler") or {}
        ft = filler.get("type")

        if ft == "class":
            return f"SC {p} some % SPLIT=|"

        if ft == "and":
            ops = filler.get("operands", [])
            named   = [op for op in ops if op.get("type") == "class"]
            some_ops = [op for op in ops if op.get("type") == "some"]
            val_ops  = [op for op in ops if op.get("type") == "value"]

            if len(named) == 1 and len(some_ops) == 1:
                c1 = iri_to_curie(named[0]["iri"], prefix_map)
                p2 = iri_to_curie(some_ops[0]["property"], prefix_map)
                return f"SC {p} some ({c1} and {p2} some %) SPLIT=|"

            if len(named) == 1 and len(val_ops) == 1:
                c1 = iri_to_curie(named[0]["iri"], prefix_map)
                p2 = iri_to_curie(val_ops[0]["property"], prefix_map)
                return f"SC {p} some ({c1} and {p2} value %) SPLIT=|"

        return None

    if t == "value":
        p = iri_to_curie(ce["property"], prefix_map)
        return f"SC {p} value %"

    return None


# ---------------------------------------------------------------------------
# Slot discovery helpers
# ---------------------------------------------------------------------------

def _disambiguate_label(label: str, ce: dict, g: Graph, prefix_map: dict) -> str:
    """Add a disambiguating parenthetical to *label* when a property has
    multiple distinct restriction patterns.  Uses the rdfs:label of the
    named intersection class as a qualifier."""
    if ce.get("type") == "some":
        filler = ce.get("filler") or {}
        if filler.get("type") == "and":
            named = [op for op in filler.get("operands", []) if op.get("type") == "class"]
            if named:
                class_label = g.value(URIRef(named[0]["iri"]), RDFS.label)
                if class_label:
                    return f"{label} ({class_label})"
        elif filler.get("type") == "class":
            class_label = g.value(URIRef(filler["iri"]), RDFS.label)
            if class_label:
                return f"{label} ({class_label})"
    if ce.get("type") == "value":
        return f"{label} (value)"
    return label


def _collect_ns_fallbacks(prop_iri: str, g: Graph, max_ns_size: int = 200) -> list:
    """Collect namespace strings from filler IRIs used for *prop_iri*.

    Checks:
      - rdfs:range values and all their transitive subclasses
      - Direct someValuesFrom fillers on this property in any restriction

    Namespaces covering more than *max_ns_size* OWL classes are excluded to
    prevent broad catch-all namespaces (e.g. the main ontology namespace) from
    flooding an enum with unrelated entries.

    Returns a sorted list of unique namespace strings."""
    prop = URIRef(prop_iri)
    ns_set: set = set()

    # From rdfs:range + their subclasses
    for r in g.objects(prop, RDFS.range):
        if isinstance(r, URIRef):
            ns = _extract_ns(str(r))
            if ns:
                ns_set.add(ns)
            for sub in all_subclasses(g, r):
                if isinstance(sub, URIRef):
                    ns = _extract_ns(str(sub))
                    if ns:
                        ns_set.add(ns)

    # From direct someValuesFrom fillers
    for bnode in g.subjects(OWL.onProperty, prop):
        if not isinstance(bnode, BNode):
            continue
        filler = g.value(bnode, OWL.someValuesFrom)
        if filler and isinstance(filler, URIRef):
            ns = _extract_ns(str(filler))
            if ns:
                ns_set.add(ns)

    # Filter out overly broad namespaces (more than max_ns_size classes)
    result = []
    for ns in sorted(ns_set):
        count = sum(
            1 for s in g.subjects(RDF.type, OWL.Class)
            if isinstance(s, URIRef) and str(s).startswith(ns)
        )
        if count <= max_ns_size:
            result.append(ns)

    return result


def _find_individual_namespace(prop_iri: str, val_prop_iri: str, g: Graph) -> str:
    """Find the most common namespace of NamedIndividuals used as hasValue fillers
    in restrictions of the form: prop some (C and val_prop value ?).
    Returns the best-matching namespace string, or '' if none found."""
    prop     = URIRef(prop_iri)
    val_prop = URIRef(val_prop_iri)
    ns_counts: dict = {}

    for bnode in g.subjects(OWL.onProperty, prop):
        if not isinstance(bnode, BNode):
            continue
        filler_node = g.value(bnode, OWL.someValuesFrom)
        if not isinstance(filler_node, BNode):
            continue
        int_list = g.value(filler_node, OWL.intersectionOf)
        if int_list is None:
            continue
        try:
            for item in Collection(g, int_list):
                if not isinstance(item, BNode):
                    continue
                if (item, OWL.onProperty, val_prop) not in g:
                    continue
                val = g.value(item, OWL.hasValue)
                if val and isinstance(val, URIRef):
                    ns = _extract_ns(str(val))
                    if ns:
                        ns_counts[ns] = ns_counts.get(ns, 0) + 1
        except Exception:
            continue

    return max(ns_counts, key=ns_counts.get) if ns_counts else ""


def _get_range_root_iris(g: Graph, query_spec: dict) -> list:
    """Return the root class IRI string(s) for an enum query.
    Used to show the user which classes define the enum's range before prompting.
    Returns [] for individuals_in_namespace (no class hierarchy to narrow)."""
    qtype = query_spec.get("type")
    if qtype == "subclasses_of":
        return [query_spec["root_iri"]]
    if qtype == "range_of_property":
        prop = URIRef(query_spec["property_iri"])
        return [str(r) for r in g.objects(prop, RDFS.range) if isinstance(r, URIRef)]
    if qtype == "roots_union":
        return list(query_spec.get("root_iris", []))
    return []


def _list_direct_subclasses(g: Graph, root_iri: str, prefix_map: dict) -> list:
    """Return sorted [(iri_str, label, curie, desc_count)] for direct
    non-deprecated labeled subclasses of root_iri.
    desc_count = number of transitive subclasses (not including the class itself)."""
    root   = URIRef(root_iri)
    result = []
    for child in g.subjects(RDFS.subClassOf, root):
        if not isinstance(child, URIRef):
            continue
        if is_deprecated(g, child):
            continue
        label = g.value(child, RDFS.label)
        if not label:
            continue
        curie = iri_to_curie(str(child), prefix_map)
        if curie.startswith("<"):
            continue
        desc_count = len(all_subclasses(g, child))
        result.append((str(child), str(label), curie, desc_count))
    return sorted(result, key=lambda x: x[1].casefold())


def discover_property_slots(
    g: Graph, root: URIRef, prefix_map: dict, rarity_threshold: float = 0.10,
    excluded_props: set = None, declined_out: list = None
) -> list:
    """Discover OWL property slots from restrictions across the subclass hierarchy.

    Patterns are grouped first by fingerprint, then collapsed by ROBOT code so
    that restrictions that differ only in a concrete filler class (e.g. two
    different data format values) produce a single generic slot.

    Properties whose usage fraction is below *rarity_threshold* are presented
    interactively (if stdin is a tty) or logged as a WARNING, so the user can
    decide whether to include them.

    *excluded_props* — set/list of property IRIs to skip without prompting.
    *declined_out*   — list to which newly-declined property IRIs are appended.

    Returned dicts contain the slot metadata plus two private keys that
    build_schema_from_owl pops before passing slots to write_schema_yaml:
      _prop_iri  — full property IRI string
      _ce        — parsed class expression dict (representative for the group)
    """
    subclasses = all_subclasses(g, root)
    active = {cls for cls in subclasses if not is_deprecated(g, cls)}
    total  = len(active)
    if total == 0:
        return []

    # property_iri → {fingerprint → {ce, users}}
    prop_patterns: dict = {}

    for cls in active:
        for sc_expr in g.objects(cls, RDFS.subClassOf):
            if isinstance(sc_expr, URIRef):
                continue        # named parent — not a restriction
            ce = parse_ce(g, sc_expr)
            if ce is None:
                continue
            t = ce.get("type")
            if t not in ("some", "value"):
                continue

            prop_iri = ce["property"]
            fp       = _ce_fingerprint(ce)

            prop_patterns.setdefault(prop_iri, {})
            prop_patterns[prop_iri].setdefault(fp, {"ce": ce, "users": set()})
            prop_patterns[prop_iri][fp]["users"].add(cls)

    # Build slot list
    slots = []

    for prop_iri, fp_patterns in prop_patterns.items():
        if not fp_patterns:
            continue
        if excluded_props and prop_iri in excluded_props:
            continue

        # --- Collapse by ROBOT code ---
        # Restrictions that differ only in their concrete filler class produce
        # identical ROBOT codes (e.g. all "has input format" restrictions map to
        # "SC P some (C and P2 some %) SPLIT=|").  Merge their user sets so we
        # get one slot per unique template code rather than one per subclass.
        robot_groups: dict = {}     # robot_code → {ce (representative), users}
        unsupported_count = 0
        for fp, info in fp_patterns.items():
            robot = _ce_to_robot(info["ce"], prefix_map)
            if robot is None:
                unsupported_count += 1
                continue
            if robot not in robot_groups:
                robot_groups[robot] = {"ce": info["ce"], "users": set()}
            robot_groups[robot]["users"].update(info["users"])

        if not robot_groups:
            if unsupported_count:
                print(
                    f"WARNING: all {unsupported_count} restriction pattern(s) for "
                    f"property {iri_to_curie(prop_iri, prefix_map)} are unsupported; "
                    f"skipped.",
                    file=sys.stderr,
                )
            continue

        if unsupported_count:
            print(
                f"WARNING: {unsupported_count} unsupported restriction pattern(s) "
                f"for property {iri_to_curie(prop_iri, prefix_map)} were skipped; "
                f"{len(robot_groups)} slot(s) generated from supported patterns.",
                file=sys.stderr,
            )

        # --- Rarity check ---
        all_users: set = set()
        for info in robot_groups.values():
            all_users.update(info["users"])
        usage_fraction = len(all_users) / total

        if usage_fraction < rarity_threshold:
            prop_curie = iri_to_curie(prop_iri, prefix_map)
            prop_lbl   = str(g.value(URIRef(prop_iri), RDFS.label) or prop_curie)
            pct        = round(usage_fraction * 100, 1)
            if sys.stdin.isatty():
                answer = input(
                    f"  Property {prop_curie} ({prop_lbl!r}) is used by only "
                    f"{len(all_users)}/{total} subclasses ({pct}%). Include? [Y/n]: "
                ).strip().lower()
                if answer == "n":
                    if declined_out is not None:
                        declined_out.append(prop_iri)
                    continue
            else:
                print(
                    f"WARNING: {prop_curie} ({prop_lbl!r}) used by {pct}% of "
                    f"subclasses — including by default (non-interactive mode).",
                    file=sys.stderr,
                )

        prop_label_str = str(g.value(URIRef(prop_iri), RDFS.label) or "")
        if not prop_label_str:
            frag = prop_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            prop_label_str = frag
            print(
                f"WARNING: property {prop_iri!r} has no rdfs:label; "
                f"using IRI fragment {frag!r} as slot name.",
                file=sys.stderr,
            )

        if len(robot_groups) > 1:
            # Categorise each ROBOT code as 'direct' (SC P some %) or
            # 'intersection' (SC P some (C and …)), then keep at most one per
            # category — the one with the most users.  This prevents O(n) slots
            # all with the same snake_case name when a property has many
            # intersection variants differing only in the named intersection class.
            def _robot_category(r: str) -> str:
                if " some % " in r or r.endswith(" some %") or r.endswith(" some % "):
                    return "direct"
                if " some (" in r:
                    return "intersection"
                if r.startswith("SC ") and " value %" in r and " some " not in r:
                    return "value"
                return "other"

            def _intersection_generality(ce_dict: dict) -> int:
                """Number of subclasses of the named intersection class.
                Higher = more general; used to prefer broad classes (e.g.
                IAO:0000027 / data entity) over specific ones when multiple
                intersection patterns exist for the same property."""
                filler = (ce_dict or {}).get("filler", {})
                if filler.get("type") != "and":
                    return 0
                named = [op for op in filler.get("operands", [])
                         if op.get("type") == "class"]
                if not named:
                    return 0
                return len(all_subclasses(g, URIRef(named[0]["iri"])))

            best: dict = {}   # category → (robot, info, score)
            for robot, info in robot_groups.items():
                cat   = _robot_category(robot)
                score = (
                    _intersection_generality(info["ce"])
                    if cat == "intersection"
                    else len(info["users"])
                )
                if cat not in best or score > best[cat][2]:
                    best[cat] = (robot, info, score)

            has_both = "intersection" in best and "direct" in best
            n_kept   = len(best)
            if n_kept < len(robot_groups):
                print(
                    f"NOTE: property {iri_to_curie(prop_iri, prefix_map)} has "
                    f"{len(robot_groups)} distinct ROBOT template patterns; "
                    f"collapsed to {n_kept} slot(s) (one per structural category).",
                    file=sys.stderr,
                )
            else:
                print(
                    f"WARNING: property {iri_to_curie(prop_iri, prefix_map)} has "
                    f"{len(robot_groups)} distinct ROBOT template patterns; generated "
                    f"{len(robot_groups)} slots. Verify names and ROBOT codes.",
                    file=sys.stderr,
                )

            for cat, (robot, info, _score) in best.items():
                ce = info["ce"]
                # When both intersection and direct slots exist, append " datasets"
                # to the direct slot's name (e.g. has_specified_data_input_datasets).
                if cat == "direct" and has_both:
                    slot_name = prop_label_str + " datasets"
                else:
                    slot_name = prop_label_str
                multivalued = "SPLIT=|" in robot

                slots.append({
                    "name":        slot_name,
                    "slot_uri":    iri_to_curie(prop_iri, prefix_map),
                    "range":       None,
                    "multivalued": multivalued,
                    "slot_group":  "Properties",
                    "robot":       robot,
                    "description": f"{prop_label_str}.",
                    "_prop_iri":   prop_iri,
                    "_ce":         ce,
                })
        else:
            # Only one pattern — use property label directly, no disambiguation.
            for robot, info in robot_groups.items():
                ce          = info["ce"]
                multivalued = "SPLIT=|" in robot

                slots.append({
                    "name":        prop_label_str,
                    "slot_uri":    iri_to_curie(prop_iri, prefix_map),
                    "range":       None,
                    "multivalued": multivalued,
                    "slot_group":  "Properties",
                    "robot":       robot,
                    "description": f"{prop_label_str}.",
                    "_prop_iri":   prop_iri,
                    "_ce":         ce,
                })

    return slots


# ---------------------------------------------------------------------------
# Enum query derivation (OWL-driven mode)
# ---------------------------------------------------------------------------

def _derive_enum_query(prop_iri: str, ce: dict, g: Graph) -> dict:
    """Determine the enum query strategy for a property's restriction pattern.
    Returns a query dict suitable for build_enum_items, or None for string fields."""
    t = ce.get("type")

    if t == "value":
        return None     # hasValue → plain string field

    if t == "some":
        filler = ce.get("filler") or {}
        ft     = filler.get("type")

        if ft == "class":
            # someValuesFrom(P, NamedClass) → range_of_property on P
            return {
                "type":         "range_of_property",
                "property_iri": prop_iri,
                "ns_fallbacks": _collect_ns_fallbacks(prop_iri, g),
            }

        if ft == "and":
            ops      = filler.get("operands", [])
            some_ops = [op for op in ops if op.get("type") == "some"]
            val_ops  = [op for op in ops if op.get("type") == "value"]

            if some_ops:
                # P some (C ∩ P2 some ?) → range_of_property on P2
                nested_prop = some_ops[0]["property"]
                return {
                    "type":         "range_of_property",
                    "property_iri": nested_prop,
                    "ns_fallbacks": _collect_ns_fallbacks(nested_prop, g),
                }

            if val_ops:
                # P some (C ∩ P2 value ?) → individuals_in_namespace
                val_prop = val_ops[0]["property"]
                ns = _find_individual_namespace(prop_iri, val_prop, g)
                if ns:
                    return {"type": "individuals_in_namespace", "namespace": ns}
                return None

    return None


def _make_enum_name(prop_label: str, ce: dict, g: Graph) -> str:
    """Generate a CamelCase enum name from a property label.
    Appends a class-name qualifier for intersection patterns to aid disambiguation."""
    words = re.sub(r'[^a-zA-Z0-9 ]', ' ', prop_label).split()
    base  = "".join(w.capitalize() for w in words) + "Menu"

    # For someValuesFrom(P, C ∩ …) patterns, add the C label as a suffix
    if ce.get("type") == "some":
        filler = ce.get("filler") or {}
        if filler.get("type") == "and":
            named = [op for op in filler.get("operands", []) if op.get("type") == "class"]
            if named:
                class_lbl = g.value(URIRef(named[0]["iri"]), RDFS.label)
                if class_lbl:
                    suffix_words = re.sub(r'[^a-zA-Z0-9 ]', ' ', str(class_lbl)).split()
                    suffix = "".join(w.capitalize() for w in suffix_words)
                    return f"{base}_{suffix}"

    return base


# ---------------------------------------------------------------------------
# Schema builder (OWL-driven mode)
# ---------------------------------------------------------------------------

def build_schema_from_owl(
    g: Graph, root_iri: str, prefix_map: dict, args, config=None
) -> tuple:
    """Build (spec_dict, enum_items) from OWL ontology, ready for write_schema_yaml().

    *args* may supply any of:
      schema_id, schema_name, schema_title, schema_version, default_prefix,
      rarity_threshold

    *config* is the loaded schema_config dict (or None/{}). Rarity-declined
    properties from this run are appended to config["excluded_properties"].
    """
    root = URIRef(root_iri)

    # --- Schema metadata ---
    ont_meta   = get_ontology_metadata(g)
    class_lbl  = str(g.value(root, RDFS.label) or "")
    class_name = "".join(
        w.capitalize()
        for w in re.sub(r'[^a-zA-Z0-9 ]', ' ', class_lbl).split()
    ) if class_lbl else "Root"

    if not ont_meta.get("title"):
        print(
            "WARNING: no dcterms:title found on owl:Ontology node; "
            "schema title auto-generated. Override with --schema-title.",
            file=sys.stderr,
        )

    ont_title = ont_meta.get("title") or ""
    ont_iri   = None
    for s in g.subjects(RDF.type, OWL.Ontology):
        ont_iri = str(s)
        break

    # Determine default prefix from root IRI
    root_curie = iri_to_curie(root_iri, prefix_map)
    auto_default = (
        root_curie.split(":")[0]
        if ":" in root_curie and not root_curie.startswith("<")
        else "ont"
    )
    class_snake = to_snake(class_name)
    # id is the ontology IRI itself; name is CamelCase form of the ontology title.
    auto_id   = ont_iri or f"http://example.org/schema/{class_snake}"
    auto_name = (
        "".join(w.capitalize() for w in ont_title.split())
        if ont_title
        else f"{class_name}Schema"
    )

    schema_meta = {
        "id":          getattr(args, "schema_id",      None) or auto_id,
        "name":        getattr(args, "schema_name",    None) or auto_name,
        "title":       getattr(args, "schema_title",   None) or ont_title or class_lbl,
        "description": ont_meta.get("description") or "",
        "version":     getattr(args, "schema_version", None) or ont_meta.get("version") or "1.0.0",
    }
    default_prefix = getattr(args, "default_prefix", None) or auto_default

    # --- Discover OWL property slots ---
    rarity         = getattr(args, "rarity_threshold", 0.10)
    excluded_props = set((config or {}).get("excluded_properties", []))
    declined_out   = []
    prop_slots     = discover_property_slots(
        g, root, prefix_map, rarity, excluded_props, declined_out
    )
    # Persist any newly declined properties into config
    if declined_out and config is not None:
        excl = config.setdefault("excluded_properties", [])
        for p in declined_out:
            if p not in excl:
                excl.append(p)

    # --- Standard annotation slots with class-specific parent range ---
    class_menu_name = f"{class_name}Menu"
    slots = []
    for s in _STANDARD_SLOTS:
        slot = dict(s)
        if slot["name"] == "parent":
            slot["range"] = class_menu_name
        slots.append(slot)

    # --- Resolve enum ranges for OWL-discovered slots ---
    enum_queries: dict   = {}   # enum_name → query_spec
    canon_to_enum: dict  = {}   # canonical key → enum_name  (dedup)

    for slot in prop_slots:
        ce       = slot.pop("_ce")
        prop_iri = slot.pop("_prop_iri")

        query = _derive_enum_query(prop_iri, ce, g)

        if query:
            enum_name = _make_enum_name(slot["name"], ce, g)
            # Canonical key for deduplication (same query → same enum)
            canon_key = (
                f"{query['type']}:"
                f"{query.get('property_iri', query.get('namespace', ''))}"
            )
            if canon_key in canon_to_enum:
                enum_name = canon_to_enum[canon_key]
            else:
                base = enum_name
                i = 1
                while enum_name in enum_queries:
                    enum_name = f"{base}{i}"
                    i += 1
                enum_queries[enum_name]  = query
                canon_to_enum[canon_key] = enum_name
            slot["range"] = enum_name
        else:
            slot["range"] = "string"

        slots.append(slot)

    # --- Build enums dict ---
    enums = {
        class_menu_name: {
            "description": (
                f"All subclasses of {iri_to_curie(root_iri, prefix_map)}, "
                f"including the root."
            ),
            "query": {
                "type":         "subclasses_of",
                "root_iri":     root_iri,
                "include_root": True,
            },
        }
    }
    for enum_name, query in enum_queries.items():
        enums[enum_name] = {
            "description": f"Permissible values for {enum_name}.",
            "query":       query,
        }

    # --- Build spec dict ---
    spec = {
        "schema":         schema_meta,
        "prefixes":       prefix_map,
        "default_prefix": default_prefix,
        "classes": [{
            "name":        class_name,
            "description": f"A {class_lbl} entry in the ontology.",
            "root_iri":    root_iri,
        }],
        "slots": slots,
        "enums": enums,
    }

    # --- Build enum items ---
    print("Building enums ...")
    enum_items: dict = {}
    for enum_name, enum_def in enums.items():
        items = build_enum_items(g, enum_def["query"], prefix_map)
        enum_items[enum_name] = items
        print(f"  {enum_name}: {len(items)} entries")
        # The root-class menu (parent choices) is expected to be large
        is_class_menu = enum_def.get("query", {}).get("type") == "subclasses_of"
        if len(items) > _LARGE_ENUM_WARN and not is_class_menu:
            print(
                f"  WARNING: {enum_name} has {len(items)} entries — the range "
                f"of its property may be too broad. Use the interactive prompt "
                f"(choose 'n' to narrow) or edit schema_config.yaml.",
                file=sys.stderr,
            )
        if len(items) == 0:
            print(
                f"  WARNING: {enum_name} has 0 entries — no classes found for "
                f"its range. The property may lack rdfs:range or its range is not "
                f"mapped by the current prefix map.",
                file=sys.stderr,
            )

    return spec, enum_items


# ---------------------------------------------------------------------------
# Interactive enum refinement
# ---------------------------------------------------------------------------

_LARGE_ENUM_WARN = 300   # re-used by interactive_refine_enums


def _apply_skip(spec: dict, enum_items: dict, enum_name: str) -> None:
    """Remove an enum from spec and enum_items; reset any referencing slot
    ranges to 'string'."""
    spec["enums"].pop(enum_name, None)
    enum_items.pop(enum_name, None)
    for slot in spec.get("slots", []):
        if slot.get("range") == enum_name:
            slot["range"] = "string"


def _select_subclasses(children: list) -> list:
    """Display a paged numbered list and let the user select subclasses.

    *children* is a list of (iri_str, label, curie, desc_count) tuples.
    Returns the list of selected tuples, or None if the user cancels."""
    PAGE  = 20
    total = len(children)

    offset = 0
    while True:
        page = children[offset:offset + PAGE]
        for idx, (_iri, label, curie, desc) in enumerate(page, start=offset + 1):
            print(f"  {idx:4}. {label:<42} ({desc:5} descendants)")

        nav_parts = []
        if offset + PAGE < total:
            nav_parts.append("(m) more")
        nav_parts.extend(["(a) all", "(Enter) cancel"])
        print("  " + "  ".join(nav_parts))

        raw = input("  Enter numbers: ").strip().lower()

        if not raw:
            return None

        if raw == "a":
            return children

        if raw == "m":
            if offset + PAGE < total:
                offset += PAGE
            continue

        # Parse comma-separated numbers
        try:
            nums = [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            print("  Invalid input — enter comma-separated numbers, 'a', 'm', or Enter.")
            continue

        invalid = [n for n in nums if n < 1 or n > total]
        if invalid:
            print(f"  Out-of-range: {invalid}.  Valid range 1–{total}.")
            continue

        selected = [children[n - 1] for n in sorted(set(nums))]
        labels_str = ", ".join(s[1] for s in selected[:3])
        if len(selected) > 3:
            labels_str += f" … (+{len(selected) - 3} more)"
        est = sum(c[3] + 1 for c in selected)   # rough estimate
        print(f"  Selected: {labels_str}  (~{est} entries)")
        confirm = input("  Confirm? [Y/n]: ").strip().lower()
        if confirm in ("", "y"):
            return selected
        # else loop again so user can re-enter


def interactive_refine_enums(
    g: Graph,
    spec: dict,
    enum_items: dict,
    prefix_map: dict,
    config: dict,
    config_path: str,
) -> tuple:
    """Interactively prompt the user to accept, narrow, or skip each enum.

    Decisions stored in *config* are applied without re-prompting.
    Returns (updated_spec, updated_enum_items, updated_config).
    """
    SEP = "\u2500" * 70   # horizontal rule

    config.setdefault("enums", {})

    # Iterate over a snapshot of keys (spec["enums"] may be mutated during iteration)
    enum_names = list(spec["enums"].keys())

    for enum_name in enum_names:
        if enum_name not in spec["enums"]:
            continue   # already removed

        enum_def   = spec["enums"][enum_name]
        query_spec = enum_def.get("query", {})
        qtype      = query_spec.get("type", "")
        items      = enum_items.get(enum_name, [])
        count      = len(items)
        can_narrow = qtype in ("subclasses_of", "range_of_property", "roots_union")

        # --- Apply saved config decision ---
        saved  = config["enums"].get(enum_name)
        action = saved.get("action") if saved else None

        if action == "accept":
            print(f"  {enum_name}: {count} entries  [config: accept]")
            continue

        if action == "skip":
            print(f"  {enum_name}: {count} entries  [config: skip — removing]")
            _apply_skip(spec, enum_items, enum_name)
            continue

        if action == "narrow" and saved.get("root_iris"):
            new_query = {
                "type":      "roots_union",
                "root_iris": saved["root_iris"],
            }
            new_items = build_enum_items(g, new_query, prefix_map)
            enum_def["query"] = new_query
            enum_items[enum_name] = new_items
            print(f"  {enum_name}: {len(new_items)} entries  [config: narrow]")
            continue

        # --- Interactive prompt (tty only) ---
        if not sys.stdin.isatty():
            # Non-interactive: no saved decision — accept as-is
            print(f"  {enum_name}: {count} entries  [no config — accepting]")
            config["enums"][enum_name] = {"action": "accept"}
            continue

        print(SEP)
        large_flag = "  \u26a0 large" if count > _LARGE_ENUM_WARN else ""
        print(f"Enum: {enum_name}  ({count} entries){large_flag}")

        root_iris = _get_range_root_iris(g, query_spec)
        for r_iri in root_iris:
            r_label = g.value(URIRef(r_iri), RDFS.label) or "?"
            r_curie = iri_to_curie(r_iri, prefix_map)
            print(f"  Range: {r_label} [{r_curie}]")

        default = "n" if (count > _LARGE_ENUM_WARN and can_narrow) else "a"
        if default == "a":
            choice_prompt = "Accept (a), narrow (n), skip (s) [A/n/s]: "
        else:
            choice_prompt = "Accept (a), narrow (n), skip (s) [a/N/s]: "

        raw    = input(choice_prompt).strip().lower()
        choice = raw[0] if raw else default

        if choice == "a":
            config["enums"][enum_name] = {"action": "accept"}
            print(f"  → Accepted {count} entries.")

        elif choice == "s":
            config["enums"][enum_name] = {"action": "skip"}
            _apply_skip(spec, enum_items, enum_name)
            print("  → Skipped.")

        elif choice == "n":
            if not can_narrow:
                print(
                    "  Narrowing is not supported for individuals_in_namespace enums. "
                    "Accepting as-is."
                )
                config["enums"][enum_name] = {"action": "accept"}
                continue

            # Gather direct subclasses from all range roots
            all_children: list = []
            for r_iri in root_iris:
                all_children.extend(_list_direct_subclasses(g, r_iri, prefix_map))

            # De-duplicate by IRI while preserving order
            seen_iris: set = set()
            uniq: list = []
            for item in all_children:
                if item[0] not in seen_iris:
                    seen_iris.add(item[0])
                    uniq.append(item)
            uniq.sort(key=lambda x: x[1].casefold())

            if not uniq:
                print("  No direct subclasses found — accepting as-is.")
                config["enums"][enum_name] = {"action": "accept"}
                continue

            selected = _select_subclasses(uniq)

            if selected is None:
                # Cancelled — accept as-is
                config["enums"][enum_name] = {"action": "accept"}
                print(f"  → Cancelled; accepted {count} entries.")

            elif len(selected) == len(uniq):
                # All selected — equivalent to accept
                config["enums"][enum_name] = {"action": "accept"}
                print(f"  → All selected; accepted {count} entries.")

            else:
                selected_iris = [s[0] for s in selected]
                new_query     = {
                    "type":      "roots_union",
                    "root_iris": selected_iris,
                }
                new_items = build_enum_items(g, new_query, prefix_map)
                enum_def["query"] = new_query
                enum_items[enum_name] = new_items
                config["enums"][enum_name] = {
                    "action":    "narrow",
                    "root_iris": selected_iris,
                    "labels":    {s[0]: s[1] for s in selected},
                }
                print(f"  → Narrowed to {len(new_items)} entries.")

        else:
            # Unrecognised input — treat as accept
            config["enums"][enum_name] = {"action": "accept"}
            print(f"  → Accepted {count} entries (unrecognised input).")

    print(SEP)
    save_schema_config(config_path, config)
    return spec, enum_items, config


# ---------------------------------------------------------------------------
# YAML writer
# ---------------------------------------------------------------------------

def _w(lines: list, text: str = "") -> None:
    lines.append(text)


def write_schema_yaml(spec: dict, enum_items: dict, output_path: str) -> None:
    """Write a robot-annotated LinkML schema.yaml from *spec* and *enum_items*."""
    L: list = []
    schema   = spec.get("schema", {})
    classes  = spec.get("classes", [])
    slots    = spec.get("slots", [])
    enums    = spec.get("enums", {})
    prefixes = spec.get("prefixes", {})

    # --- header ---
    _w(L, f"id: {schema.get('id', '')}")
    _w(L, f"name: {schema.get('name', '')}")
    _w(L, f"title: {schema.get('title', '')}")
    if schema.get("description"):
        _w(L, "description: >-")
        for line in schema["description"].strip().splitlines():
            _w(L, f"  {line.strip()}")
    _w(L, f"version: {schema.get('version', '1.0.0')}")
    _w(L, "annotations:")
    _w(L, '  list_delimiter: "|"')
    _w(L)
    _w(L, "imports:")
    _w(L, "  - linkml:types")
    _w(L)
    _w(L, "prefixes:")
    for pfx, ns in prefixes.items():
        _w(L, f"  {pfx}: {ns}")
    _w(L)
    _w(L, f"default_prefix: {spec.get('default_prefix', '')}")
    _w(L, "default_range: string")
    _w(L)

    # --- classes ---
    _w(L, "classes:")
    _w(L)
    for cls_def in classes:
        cls_name = cls_def["name"]
        _w(L, f"  {cls_name}:")
        _w(L, f"    name: {cls_name}")
        _w(L, f"    title: {cls_name}")
        if cls_def.get("description"):
            _w(L, "    description: >-")
            _w(L, f"      {cls_def['description'].strip()}")
        _w(L, "    slots:")
        for slot in slots:
            _w(L, f"      - {to_snake(slot['name'])}")
        _w(L, "    slot_usage:")
        for rank, slot in enumerate(slots, start=1):
            snake = to_snake(slot["name"])
            _w(L, f"      {snake}:")
            _w(L, f"        rank: {rank}")
            if slot.get("slot_group"):
                _w(L, f"        slot_group: {slot['slot_group']}")
    _w(L)

    # --- slots ---
    _w(L, "slots:")
    _w(L)
    for slot in slots:
        name  = slot["name"]
        snake = to_snake(name)
        robot = slot.get("robot", "")
        desc  = (slot.get("description") or "").strip()

        _w(L, f"  {snake}:")
        _w(L, f"    name: {snake}")
        _w(L, f"    title: {name}")
        if robot:
            _w(L, "    annotations:")
            _w(L, f"      robot: '{robot}'")
        if desc:
            _w(L, "    description: >-")
            words = desc.split()
            line  = ""
            for word in words:
                if len(line) + len(word) + 1 > 70:
                    _w(L, f"      {line.rstrip()}")
                    line = word + " "
                else:
                    line += word + " "
            if line.strip():
                _w(L, f"      {line.rstrip()}")
        if slot.get("slot_uri"):
            _w(L, f"    slot_uri: {slot['slot_uri']}")
        if slot.get("identifier"):
            _w(L, "    identifier: true")
        if slot.get("unique"):
            _w(L, "    unique: true")
        if slot.get("required"):
            _w(L, "    required: true")
        if slot.get("multivalued"):
            _w(L, "    multivalued: true")
        rng = "uriorcurie" if robot == "ID" else (slot.get("range") or "string")
        _w(L, f"    range: {rng}")
        examples = slot.get("examples") or []
        if examples:
            _w(L, "    examples:")
            for ex in examples:
                _w(L, f"      - value: {ex}")
        _w(L)

    # --- enums ---
    _w(L, "enums:")
    _w(L)
    for enum_name, enum_def in enums.items():
        items = enum_items.get(enum_name, [])
        _w(L, f"  {enum_name}:")
        _w(L, f"    name: {enum_name}")
        if enum_def.get("description"):
            _w(L, f"    description: {enum_def['description']}")
        _w(L, "    permissible_values:")
        for curie, label, _iri, parent_curie in items:
            # Key and text are the CURIE (always unique); human-readable
            # label goes in title for DataHarmonizer display.
            # is_a encodes OWL subClassOf hierarchy for entries deeper than
            # one level from the enum root (direct children of root get no is_a).
            _w(L, f"      {curie}:")
            _w(L, f"        text: {curie}")
            _w(L, f"        title: {label}")
            _w(L, f"        meaning: {curie}")
            if parent_curie:
                _w(L, f"        is_a: {parent_curie}")
        _w(L)

    # --- types ---
    _w(L, "types:")
    _w(L)
    _w(L, "  WhitespaceMinimizedString:")
    _w(L, "    name: WhitespaceMinimizedString")
    _w(L, "    typeof: string")
    _w(L, "    description: >-")
    _w(L, "      A string with leading/trailing whitespace stripped and")
    _w(L, "      internal whitespace collapsed to single spaces.")
    _w(L, "    base: str")
    _w(L, "    uri: xsd:token")
    _w(L)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"  {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # --- Common args ---
    parser.add_argument(
        "--spec", metavar="YAML",
        help="Schema specification YAML file (spec-driven / legacy mode).",
    )
    parser.add_argument(
        "--ontology", metavar="OWL",
        help=(
            "Path to the OWL ontology file. "
            "Required in OWL-driven mode; overrides 'ontology:' in spec-driven mode."
        ),
    )
    parser.add_argument(
        "--output", metavar="YAML",
        help="Output path for schema.yaml. Defaults to schema.yaml beside spec/ontology.",
    )

    # --- OWL-driven mode args ---
    parser.add_argument(
        "--class", dest="root_class", metavar="IRI",
        help="Root class IRI for OWL-driven mode.",
    )
    parser.add_argument(
        "--also-load", dest="also_load", metavar="OWL", action="append", default=[],
        help=(
            "Additional OWL file(s) to merge into the graph before processing. "
            "Repeatable. Use when axioms are split across component files "
            "(e.g. after robot unmerge)."
        ),
    )
    parser.add_argument(
        "--prefixes", metavar="YAML",
        help="YAML file mapping prefix→namespace (OWL-driven mode).",
    )
    parser.add_argument("--schema-id",      metavar="IRI",  dest="schema_id")
    parser.add_argument("--schema-name",    metavar="STR",  dest="schema_name")
    parser.add_argument("--schema-title",   metavar="STR",  dest="schema_title")
    parser.add_argument("--schema-version", metavar="STR",  dest="schema_version")
    parser.add_argument("--default-prefix", metavar="STR",  dest="default_prefix")
    parser.add_argument(
        "--rarity-threshold", metavar="FLOAT", type=float, default=0.10,
        dest="rarity_threshold",
        help=(
            "0–1 fraction; properties used by fewer subclasses than this "
            "fraction prompt interactively. Default: 0.10."
        ),
    )
    parser.add_argument(
        "--config", metavar="YAML", dest="config_path",
        help=(
            "Path to schema_config.yaml for persisting enum decisions. "
            "Defaults to schema_config.yaml beside --output."
        ),
    )
    args = parser.parse_args()

    # --- Validate mode ---
    if not args.spec and not args.root_class:
        parser.error(
            "Provide --spec (spec-driven mode) or --class IRI (OWL-driven mode)."
        )
    if args.spec and args.root_class:
        print(
            "WARNING: both --spec and --class provided; --spec takes precedence.",
            file=sys.stderr,
        )

    # -----------------------------------------------------------------------
    # Spec-driven mode (legacy)
    # -----------------------------------------------------------------------
    if args.spec:
        spec_path = os.path.abspath(args.spec)
        spec_dir  = os.path.dirname(spec_path)
        with open(spec_path, encoding="utf-8") as f:
            spec = yaml.safe_load(f)

        owl_path = args.ontology
        if not owl_path:
            spec_owl = spec.get("ontology")
            if not spec_owl:
                print(
                    "ERROR: provide an ontology path via --ontology or the "
                    "'ontology:' key in the spec file.",
                    file=sys.stderr,
                )
                sys.exit(1)
            owl_path = os.path.join(spec_dir, spec_owl)
        owl_path = os.path.abspath(owl_path)

        output_path = os.path.abspath(
            args.output if args.output else os.path.join(spec_dir, "schema.yaml")
        )

        prefix_map: dict = {
            pfx.rstrip(":"): ns
            for pfx, ns in spec.get("prefixes", {}).items()
        }

        print(f"Loading {owl_path} ...")
        g = Graph()
        g.parse(owl_path, format="xml")
        for extra in args.also_load:
            extra_path = os.path.abspath(extra)
            print(f"  also loading {extra_path} ...")
            g.parse(extra_path, format="xml")
        print(f"  {len(g)} triples loaded")

        print("Building enums ...")
        enum_items: dict = {}
        for enum_name, enum_def in spec.get("enums", {}).items():
            items = build_enum_items(g, enum_def.get("query", {}), prefix_map)
            enum_items[enum_name] = items
            print(f"  {enum_name}: {len(items)} entries")

        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        print(f"\nWriting {output_path} ...")
        write_schema_yaml(spec, enum_items, output_path)
        print("Done.")
        return

    # -----------------------------------------------------------------------
    # OWL-driven mode
    # -----------------------------------------------------------------------
    if not args.ontology:
        parser.error("--ontology is required in OWL-driven mode.")

    owl_path    = os.path.abspath(args.ontology)
    owl_dir     = os.path.dirname(owl_path)
    output_path = os.path.abspath(
        args.output if args.output else os.path.join(owl_dir, "schema.yaml")
    )

    # Resolve config path and load existing decisions
    config_path = os.path.abspath(
        args.config_path if args.config_path
        else os.path.join(os.path.dirname(output_path), "schema_config.yaml")
    )
    config = load_schema_config(config_path)
    if os.path.exists(config_path):
        print(f"Config loaded from {config_path}")

    # Load ontology (and any additional component files)
    print(f"Loading {owl_path} ...")
    g = Graph()
    g.parse(owl_path, format="xml")
    for extra in args.also_load:
        extra_path = os.path.abspath(extra)
        print(f"  also loading {extra_path} ...")
        g.parse(extra_path, format="xml")
    print(f"  {len(g)} triples loaded")

    # Build prefix map
    if args.prefixes:
        prefixes_path = os.path.abspath(args.prefixes)
        with open(prefixes_path, encoding="utf-8") as f:
            user_prefixes = yaml.safe_load(f) or {}
        prefix_map = {pfx.rstrip(":"): ns for pfx, ns in user_prefixes.items()}
        print(f"Prefixes loaded from {prefixes_path} ({len(prefix_map)} entries)")
    else:
        print("Discovering prefixes from ontology ...")
        prefix_map = build_prefix_map_from_owl(owl_path, g)
        print(f"  {len(prefix_map)} prefixes resolved")

    # Build schema (rarity prompts may appear here; decisions stored in config)
    print(f"Discovering schema structure for <{args.root_class}> ...")
    spec, enum_items = build_schema_from_owl(g, args.root_class, prefix_map, args, config)

    # Apply config decisions and (if stdin is a terminal) interactively prompt
    # for enums without saved decisions.  Called in both interactive and
    # non-interactive mode so that config decisions are always applied.
    print("\nReviewing enums ...")
    spec, enum_items, config = interactive_refine_enums(
        g, spec, enum_items, prefix_map, config, config_path
    )

    # Write output
    print(f"\nWriting {output_path} ...")
    write_schema_yaml(spec, enum_items, output_path)
    print("Done.")


if __name__ == "__main__":
    main()
