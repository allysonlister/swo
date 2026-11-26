#!/usr/bin/env python3
"""
owl_to_schema.py — Generate a robot-annotated LinkML schema.yaml
                   from an OWL ontology.

Two operating modes
-------------------

OWL-driven mode  (--class required, --spec not needed):
  Derives all schema structure directly from OWL restrictions and annotations.
  Every object-property restriction found across the subclass hierarchy becomes
  a slot; standard annotation slots (ID, label, parent, definition, …) are
  always prepended automatically.

  Output file names are derived from the ontology's default_prefix (the CURIE
  namespace of the root class): a root class in the SWO prefix namespace
  produces SWO_schema.yaml, SWO_schema_config.yaml, and SWO_schema_data.json.
  Falls back to the uppercased OWL filename stem when the IRI is unresolvable.

  python owl_to_schema.py \\
      --ontology swo-edit.owl \\
      --class    http://purl.obolibrary.org/obo/SWO_0000001

  Optional flags (OWL-driven mode):
    --output    swo_schema.yaml     override default output path
    --prefixes  prefixes.yaml      prefix→namespace map; auto-discovered if omitted
    --config    swo_schema_config.yaml persist enum accept/narrow/skip decisions
    --schema-id    IRI             override auto-generated schema @id
    --schema-name  STR             override auto-generated schema name
    --schema-title STR             override auto-generated schema title
    --schema-version STR           override auto-generated schema version
    --default-prefix STR           override auto-detected default prefix
    --rarity-threshold FLOAT       0–1 fraction; properties used by fewer
                                   subclasses prompt interactively (default 0.10)

Spec-driven mode  (--spec required, legacy):
  Reads schema structure from a hand-authored YAML spec file.

  python owl_to_schema.py \\
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
import json
import os
import re
import shutil
import subprocess
import sys
import csv
import tempfile

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required.  pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS


# ---------------------------------------------------------------------------
# Well-known namespace → canonical prefix table
# ---------------------------------------------------------------------------

WELL_KNOWN_PREFIXES = {
    # Prefixes not in ROBOT's default OBO table that owl_to_schema.py needs.
    # These supplement (and override) the robot export-prefixes output.
    "https://w3id.org/linkml/":               "linkml",
    "http://purl.org/dc/terms/":              "dcterms",
    "http://purl.org/dc/elements/1.1/":       "dc",
    "http://edamontology.org/":               "EDAM",
    "http://kt.ijs.si/panovp/OntoDM#":        "ODME",
    "http://www.ontodm.com/OntoDM-core/":     "OntoDM",
}

# Common OWL release-stage suffixes that are stripped when deriving the
# ontology prefix from the file name (order matters: longest match wins).
_OWL_STEM_SUFFIXES = ("-merged", "-simple", "-full", "-base", "-core", "-edit")


def _derive_ontology_prefix(owl_path: str) -> str:
    """Derive a short uppercase prefix from an OWL file path.

    Used as a fallback when the root-class IRI cannot be resolved to a known
    CURIE namespace.  Strips the directory, extension, and any common
    release-stage suffix, then uppercases per OBO convention:
    ``swo-edit.owl`` → ``SWO``.
    """
    stem = os.path.splitext(os.path.basename(owl_path))[0]
    for suffix in _OWL_STEM_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem.upper()


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
        # TODO: promote to "AL rdfs:label@en" once swo-edit.owl plain-literal
        # annotations are upgraded to language-tagged (@en) literals throughout.
        "robot": "A rdfs:label",
        "description": "Primary English name (rdfs:label) of this class.",
    },
    {
        "name": "exact synonym",
        "slot_uri": "IAO:0000118",
        "range": "WhitespaceMinimizedString",
        "multivalued": True,
        "slot_group": "Ontology Metadata",
        "robot": "A IAO:0000118 SPLIT=|",
        "description": (
            "Exact synonym(s) (IAO:0000118) for this class. "
            "Separate multiple values with |."
        ),
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
        # TODO: promote to "AL IAO:0000115@en" once swo-edit.owl plain-literal
        # annotations are upgraded to language-tagged (@en) literals throughout.
        "robot": "A IAO:0000115",
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
        # TODO: promote to "AL rdfs:comment@en" once swo-edit.owl plain-literal
        # annotations are upgraded to language-tagged (@en) literals throughout.
        "robot": "A rdfs:comment",
        "description": "Free-text rdfs:comment annotation.",
    },
    {
        "name": "term creator",
        "slot_uri": "dcterms:creator",
        "range": "WhitespaceMinimizedString",
        "multivalued": True,
        "slot_group": "Ontology Metadata",
        "robot": "A dcterms:creator SPLIT=|",
        "people_menu": "TermCreatorMenu",
        "description": (
            "Name(s) of the curator(s) who added this entry (dcterms:creator). "
            "Separate multiple values with |."
        ),
    },
    {
        "name": "contributor",
        "slot_uri": "dc:contributor",
        "range": "WhitespaceMinimizedString",
        "multivalued": True,
        "slot_group": "Ontology Metadata",
        "robot": "A dc:contributor SPLIT=|",
        "people_menu": "ContributorMenu",
        "description": (
            "Name(s) of contributor(s) (dc:contributor). "
            "Separate multiple values with |."
        ),
    },
    {
        "name": "dcterms contributor",
        "slot_uri": "dcterms:contributor",
        "range": "WhitespaceMinimizedString",
        "multivalued": True,
        "slot_group": "Ontology Metadata",
        "robot": "A dcterms:contributor SPLIT=|",
        "people_menu": "DctermsContributorMenu",
        "description": (
            "Name(s) of contributor(s) (dcterms:contributor). "
            "Separate multiple values with |."
        ),
    },
    {
        "name": "term editor",
        "slot_uri": "IAO:0000117",
        "range": "WhitespaceMinimizedString",
        "multivalued": True,
        "slot_group": "Ontology Metadata",
        "robot": "A IAO:0000117 SPLIT=|",
        "people_menu": "TermEditorMenu",
        "description": (
            "Name(s) of the editor(s) for this term (IAO:0000117). "
            "Separate multiple values with |."
        ),
    },
]


# ---------------------------------------------------------------------------
# OWL helpers
# ---------------------------------------------------------------------------

def all_subclasses(g: Graph, root: URIRef) -> set:
    """Return all non-deprecated transitive subclasses of *root* (not including
    root itself).  Deprecated classes are excluded from the result but are still
    traversed so that any active classes beneath them are not missed."""
    visited: set = set()   # all visited nodes (to prevent cycles)
    result:  set = set()   # non-deprecated nodes only
    queue = [s for s in g.subjects(RDFS.subClassOf, root) if isinstance(s, URIRef)]
    while queue:
        cls = queue.pop()
        if cls in visited:
            continue
        visited.add(cls)
        if not is_deprecated(g, cls):
            result.add(cls)
        queue.extend(
            s for s in g.subjects(RDFS.subClassOf, cls) if isinstance(s, URIRef)
        )
    return result


def is_deprecated(g: Graph, uri: URIRef) -> bool:
    """Return True if *uri* carries owl:deprecated true."""
    return bool(g.value(uri, OWL.deprecated))


def _yaml_scalar(s: str) -> str:
    """Return s as a safe YAML scalar, double-quoting when the string contains
    characters that would be misread by a YAML parser (any colon, leading special
    chars, empty string, or any value YAML would parse as a non-string type such
    as integers, floats, booleans, or null)."""
    need_quote = (
        not s
        or ":" in s
        or s[0] in "#&*!|>'\",%@`{["
        or not isinstance(yaml.safe_load(s), str)   # e.g. 1, 6.02, true, null
    )
    if need_quote:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


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
      2. Only when no range is declared: add every OWL class found in each
         *ns_fallbacks* namespace and recurse into their subclasses.
         (ns_fallbacks are intentionally skipped when a range is found, to
         avoid bloating the enum with the entire SWO namespace.)
    """
    prop  = URIRef(prop_iri)
    roots = {o for o in g.objects(prop, RDFS.range)
             if isinstance(o, URIRef) and not is_deprecated(g, o)}

    result: set = set()
    for root in roots:
        result.add(root)
        result.update(all_subclasses(g, root))

    if not roots:
        fallback_new: set = set()
        for ns in ns_fallbacks:
            for s in g.subjects(RDF.type, OWL.Class):
                if (isinstance(s, URIRef) and str(s).startswith(ns)
                        and s not in result and not is_deprecated(g, s)):
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
        include_self = query_spec.get("include_self", False)
        if include_self or query_spec.get("include_root", True):
            cls_set = cls_set | {root}
        # With include_self: root is a regular member so direct children get
        # is_a: <root_label>.  Without it: root is treated as an implicit root
        # and direct children get no is_a (they're level-1 entries).
        root_iris = frozenset() if include_self else frozenset([root])
        return classes_to_lookup_rows(g, cls_set, prefix_map,
                                      root_iris=root_iris)

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
        "# enums.<name>.enum_uri: CURIE — manually pins enum_uri (overrides graph-derived value)\n"
        "# enums.<name>.include_self: true — include the root class as a permissible value\n"
        "# excluded_properties: property IRIs skipped by rarity prompt\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=True)
    print(f"Config saved to {path}")


def _write_enum_config(
    config_enums: dict, name: str, entry: dict, saved: dict | None
) -> None:
    """Write *entry* to config_enums[name], preserving any fields from *saved*
    that the new *entry* does not explicitly override (e.g. ``enum_uri``,
    ``include_self``)."""
    if saved:
        preserved = {k: v for k, v in saved.items() if k not in entry}
        entry = {**preserved, **entry}
    config_enums[name] = entry


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

def _extract_ns(iri: str) -> str:
    """Extract namespace from an IRI using the numeric-segment heuristic.
    Returns the IRI up to and including the last separator before an all-digit
    local part (e.g. 'http://…/license/SWO_1000006' → 'http://…/license/SWO_').
    Returns '' if the heuristic does not match."""
    m = re.match(r'^(.*[/_])(\d+)$', iri)
    return m.group(1) if m else ""


def build_prefix_map_from_owl(owl_path: str, g: Graph) -> dict:
    """Build a prefix→namespace map from ROBOT's default OBO prefix table,
    supplemented by WELL_KNOWN_PREFIXES for namespaces ROBOT does not carry
    (linkml, dcterms, EDAM, ODME, dc).

    Falls back to WELL_KNOWN_PREFIXES alone if `robot` is not on PATH.
    Pass --prefixes to override this entirely with a hand-curated file."""

    result: dict = {}

    if shutil.which("robot") is not None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                ["robot", "export-prefixes", "--output", tmp_path],
                check=True, capture_output=True,
            )
            with open(tmp_path, encoding="utf-8") as fh:
                data = json.load(fh)
            for pfx, val in data.get("@context", {}).items():
                ns = val.get("@id") if isinstance(val, dict) else val
                if ns:
                    result[pfx] = ns
        except Exception as exc:
            print(f"WARNING: robot export-prefixes failed ({exc}); "
                  "falling back to WELL_KNOWN_PREFIXES only.", file=sys.stderr)
            result = {}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    else:
        print("WARNING: `robot` not found on PATH; "
              "prefix map limited to WELL_KNOWN_PREFIXES.", file=sys.stderr)

    # Supplement/override with WELL_KNOWN_PREFIXES (linkml, dcterms, EDAM, ODME, dc)
    for ns, pfx in WELL_KNOWN_PREFIXES.items():
        result[pfx] = ns

    return result


# ---------------------------------------------------------------------------
# OWL file loader (handles RDF/XML and OFN via ROBOT conversion)
# ---------------------------------------------------------------------------

def _load_owl_to_graph(g: Graph, owl_path: str) -> None:
    """Parse an OWL file into *g*.

    rdflib supports RDF/XML, Turtle, N3 etc. but not OWL Functional Notation
    (OFN).  OFN files (those starting with 'Prefix(' or 'Ontology(') are first
    converted to Turtle via ROBOT, then loaded from the temp Turtle file.
    """
    with open(owl_path, "rb") as fh:
        head = fh.read(16).lstrip()

    is_ofn = head.startswith(b"Prefix(") or head.startswith(b"Ontology(")

    if not is_ofn:
        g.parse(owl_path, format="xml")
        return

    if shutil.which("robot") is None:
        raise RuntimeError(
            f"{owl_path!r} is in OWL Functional Notation (OFN) but `robot` "
            "is not on PATH.  Install ROBOT to load OFN files."
        )
    with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["robot", "convert", "--input", owl_path, "--format", "ttl",
             "--output", tmp_path],
            check=True, capture_output=True,
        )
        g.parse(tmp_path, format="turtle")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Ontology metadata
# ---------------------------------------------------------------------------

def get_ontology_metadata(g: Graph) -> dict:
    """Query the owl:Ontology node for dcterms:title, dcterms:description,
    and version.  Returns {title, description, version} with None values
    for missing fields.

    Version resolution order:
    1. owl:versionInfo (plain string annotation, e.g. "1.9.0")
    2. Last path segment of owl:versionIRI (OBO convention:
       http://purl.obolibrary.org/obo/swo.owl/1.9.0 → "1.9.0")
    """
    DCTERMS = "http://purl.org/dc/terms/"
    ont = None
    for s in g.subjects(RDF.type, OWL.Ontology):
        ont = s
        break
    if ont is None:
        return {"title": None, "description": None, "version": None}

    version = None
    if v := g.value(ont, OWL.versionInfo):
        version = str(v)
    elif v := g.value(ont, OWL.versionIRI):
        version = str(v).rstrip("/").split("/")[-1]

    return {
        "title":       str(t) if (t := g.value(ont, URIRef(DCTERMS + "title"))) else None,
        "description": str(d) if (d := g.value(ont, URIRef(DCTERMS + "description"))) else None,
        "version":     version,
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
            return {
                "type": "value",
                "property": prop_iri,
                "value": str(val),
                "value_is_uri": isinstance(val, URIRef),
            }

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
        return f"value:{ce['property']}:{ce.get('value', '')}"
    if t == "all":
        return f"all:{ce['property']}:{_ce_fingerprint(ce.get('filler'))}"
    if t == "and":
        ops = sorted(_ce_fingerprint(op) for op in ce.get("operands", []))
        return f"and:({'|'.join(ops)})"
    return str(ce)


def _ce_to_robot(ce: dict, prefix_map: dict, g=None) -> str:
    """Convert a parsed CE dict to a ROBOT template code string.
    Returns None for unsupported patterns (complex intersections).

    When *g* (an rdflib Graph) is provided, the property is referenced by its
    quoted rdfs:label (e.g. ``'has specified data input'``), which ROBOT resolves
    to the property IRI at template-compile time.  This is more readable and
    matches ROBOT's label-resolution behaviour used for value cells.
    Falls back to a CURIE when no rdfs:label is found.

    Restriction type determines the ROBOT quantifier:
      someValuesFrom  → ``SC 'prop' some % SPLIT=|``   (class range)
      hasValue (URI)  → ``SC 'prop' value % SPLIT=|``  (named individual range)
      hasValue (lit.) → ``SC 'prop' value %``           (data/literal value)
    """
    if ce is None:
        return None
    t = ce.get("type")

    def _prop_ref(iri: str) -> str:
        if g is not None:
            lbl = g.value(URIRef(iri), RDFS.label)
            if lbl:
                return f"'{lbl}'"
        return iri_to_curie(iri, prefix_map)

    if t == "some":
        p = _prop_ref(ce["property"])
        filler = ce.get("filler") or {}
        ft = filler.get("type")

        if ft == "class":
            return f"SC {p} some % SPLIT=|"

        if ft == "and":
            # ROBOT template engine cannot parse nested restrictions inside
            # intersections (e.g. "P some (C and P2 some %)"), so we emit the
            # simplified outer someValuesFrom form.  The extraction code puts the
            # inner target class CURIE in the cell, so the generated axiom is
            # "SubClassOf(P some Target)" rather than the full intersection.
            return f"SC {p} some % SPLIT=|"

        return None

    if t == "value":
        p = _prop_ref(ce["property"])
        if ce.get("value_is_uri"):
            # Named-individual (hasValue) range: use 'value' restriction.
            # SPLIT=| supports properties whose target is multi-valued per class.
            return f"SC {p} value % SPLIT=|"
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
        return [str(r) for r in g.objects(prop, RDFS.range)
                if isinstance(r, URIRef) and not is_deprecated(g, r)]
    if qtype == "roots_union":
        return list(query_spec.get("root_iris", []))
    return []


def _enum_single_root_iri(g: Graph, enum_def: dict) -> str | None:
    """Return the single root IRI string for an enum's query, or None when
    there is not exactly one (multi-root unions, range_of_property with
    multiple ranges, or individuals_in_namespace)."""
    query = enum_def.get("query", {})
    qtype = query.get("type")
    if qtype == "subclasses_of":
        return query.get("root_iri")
    if qtype == "roots_union":
        iris = query.get("root_iris", [])
        return iris[0] if len(iris) == 1 else None
    if qtype == "range_of_property":
        prop   = URIRef(query["property_iri"])
        ranges = [str(o) for o in g.objects(prop, RDFS.range)
                  if isinstance(o, URIRef) and not is_deprecated(g, o)]
        return ranges[0] if len(ranges) == 1 else None
    return None   # individuals_in_namespace: no class root


def _enrich_enum_uris(g: Graph, spec: dict, prefix_map: dict) -> None:
    """Add enum_uri to every enum def that has exactly one root class.
    Called unconditionally (not gated on --scope) since enum_uri is pure
    annotation metadata useful to any LinkML consumer."""
    for enum_def in spec.get("enums", {}).values():
        root_iri = _enum_single_root_iri(g, enum_def)
        if root_iri:
            curie = iri_to_curie(root_iri, prefix_map)
            if not curie.startswith("<"):
                enum_def["enum_uri"] = curie


def _build_scope_classes(
    g: Graph,
    spec: dict,
    enum_items: dict,
    prefix_map: dict,
) -> tuple:
    """Return (new_class_defs, new_enum_defs, new_enum_items) for --scope 1.

    For each enum whose permissible values come from a single OWL parent class,
    produce:
      • a class def (standard annotation slots only, parent range → <Name>Menu)
      • a <Name>Menu enum for the parent slot's range
      • enum items for that menu

    Skips the main class's own root and deduplicates when two enums share the
    same root IRI.
    """
    main_root_iris = {
        cls["root_iri"] for cls in spec.get("classes", []) if "root_iri" in cls
    }
    seen           = set(main_root_iris)
    std_slot_names = [to_snake(s["name"]) for s in _STANDARD_SLOTS]

    new_classes: list = []
    new_enums:   dict = {}
    new_items:   dict = {}

    for enum_def in spec["enums"].values():
        root_iri = _enum_single_root_iri(g, enum_def)
        if not root_iri or root_iri in seen:
            continue
        label = str(g.value(URIRef(root_iri), RDFS.label) or "")
        if not label:
            continue
        seen.add(root_iri)

        cls_name   = "".join(
            w.capitalize()
            for w in re.sub(r"[^a-zA-Z0-9 ]", " ", label).split()
        )
        menu_name  = f"{cls_name}Menu"
        root_curie = iri_to_curie(root_iri, prefix_map)

        new_classes.append({
            "name":         cls_name,
            "description":  f"A {label} entry referenced by the ontology.",
            "root_iri":     root_iri,
            "slot_names":   std_slot_names,
            "parent_range": menu_name,
        })

        menu_query = {
            "type":         "subclasses_of",
            "root_iri":     root_iri,
            "include_root": True,
        }
        menu_def = {
            "description": (
                f"All subclasses of {root_curie}, including the root."
            ),
            "query": menu_query,
        }
        if not root_curie.startswith("<"):
            menu_def["enum_uri"] = root_curie
        new_enums[menu_name] = menu_def
        new_items[menu_name] = build_enum_items(g, menu_query, prefix_map)

    return new_classes, new_enums, new_items


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
        # someValuesFrom → SPLIT=| (multivalued by OWL semantics)
        # hasValue URI   → SPLIT=| (named-individual range, can be multi-valued)
        # hasValue lit.  → no SPLIT=| (data/literal value, singular)
        robot_groups: dict = {}     # robot_code → {ce (representative), users}
        unsupported_count = 0
        for fp, info in fp_patterns.items():
            robot = _ce_to_robot(info["ce"], prefix_map, g)
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
                # intersection must be checked before direct: intersection
                # codes also contain "some" and "%", but always have "some (".
                if " some (" in r:
                    return "intersection"
                if " some " in r and "%" in r:
                    return "direct"
                if r.startswith("SC ") and " value " in r and " some " not in r:
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
                # When both intersection and direct slots exist, skip the
                # intersection slot: it involves complex nested axioms
                # (someValuesFrom(C and P some X)) that cannot be cleanly
                # represented as a simple ROBOT template column.
                # The direct someValuesFrom slot is kept under the plain
                # property label.  A future --robot TSV pass can omit the
                # intersection values or handle them separately.
                if cat == "intersection" and has_both:
                    print(
                        f"NOTE: Omitting '{prop_label_str} datasets' slot for "
                        f"{iri_to_curie(prop_iri, prefix_map)}: intersection "
                        f"axiom pattern (someValuesFrom(C and P some X)) cannot "
                        f"be represented as a simple ROBOT template column.",
                        file=sys.stderr,
                    )
                    continue

                slot_name   = prop_label_str
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
    cfg_enums = (config or {}).get("enums", {})
    for enum_name, enum_def in enums.items():
        query = dict(enum_def["query"])  # copy so we don't mutate the spec
        if cfg_enums.get(enum_name, {}).get("include_self"):
            query["include_self"] = True
        items = build_enum_items(g, query, prefix_map)
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
            _write_enum_config(config["enums"], enum_name, {"action": "accept"}, saved)
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
            _write_enum_config(config["enums"], enum_name, {"action": "accept"}, saved)
            print(f"  → Accepted {count} entries.")

        elif choice == "s":
            _write_enum_config(config["enums"], enum_name, {"action": "skip"}, saved)
            _apply_skip(spec, enum_items, enum_name)
            print("  → Skipped.")

        elif choice == "n":
            if not can_narrow:
                print(
                    "  Narrowing is not supported for individuals_in_namespace enums. "
                    "Accepting as-is."
                )
                _write_enum_config(config["enums"], enum_name, {"action": "accept"}, saved)
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
                _write_enum_config(config["enums"], enum_name, {"action": "accept"}, saved)
                continue

            selected = _select_subclasses(uniq)

            if selected is None:
                # Cancelled — accept as-is
                _write_enum_config(config["enums"], enum_name, {"action": "accept"}, saved)
                print(f"  → Cancelled; accepted {count} entries.")

            elif len(selected) == len(uniq):
                # All selected — equivalent to accept
                _write_enum_config(config["enums"], enum_name, {"action": "accept"}, saved)
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
                _write_enum_config(config["enums"], enum_name, {
                    "action":    "narrow",
                    "root_iris": selected_iris,
                    "labels":    {s[0]: s[1] for s in selected},
                }, saved)
                print(f"  → Narrowed to {len(new_items)} entries.")

        else:
            # Unrecognised input — treat as accept
            _write_enum_config(config["enums"], enum_name, {"action": "accept"}, saved)
            print(f"  → Accepted {count} entries (unrecognised input).")

    print(SEP)
    save_schema_config(config_path, config)
    return spec, enum_items, config


# ---------------------------------------------------------------------------
# YAML writer
# ---------------------------------------------------------------------------

def _w(lines: list, text: str = "") -> None:
    lines.append(text)


def write_schema_yaml(
    spec: dict,
    enum_items: dict,
    output_path: str,
    prefix_map: dict | None = None,
) -> None:
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
    _w(L, '  application: robot')
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

    # --- push-down: slot attributes overridden in any class's slot_usage ---
    # If class A overrides attribute X on slot S but class B does not, the
    # global schema.slots.S.X value bleeds into class B via inheritance and
    # DataHarmonizer honours the global over the per-class slot_usage.  The
    # fix: drop attribute X from schema.slots.S and stamp it explicitly into
    # every class's slot_usage.S — using the class-specific override where
    # one exists, and the original global value everywhere else.
    #
    # Currently only 'range' is overridden per-class (via cls_def parent_range).
    # slot_range_overrides: {slot_snake: {cls_name: override_range_str}}
    slot_range_overrides: dict = {}
    for cls_def in classes:
        override = cls_def.get("parent_range")
        if override:
            slot_range_overrides.setdefault("parent", {})[cls_def["name"]] = override

    # Pre-compute the global range for each pushed-down slot so we can
    # explicitly stamp it on classes that have no per-class override.
    global_slot_range: dict = {}  # {slot_snake: range_str}
    for slot in slots:
        snake = to_snake(slot["name"])
        if snake in slot_range_overrides:
            robot_code = slot.get("robot", "")
            rng = "uriorcurie" if robot_code == "ID" else (slot.get("range") or "string")
            global_slot_range[snake] = rng

    # --- parent-enum enum_uri alignment ---
    # For each class with a class_uri, set its parent-slot menu enum's
    # enum_uri to match the class_uri.  This signals to DataHarmonizer
    # that new rows added to the class tab will cause that enum to be
    # dynamically regenerated from the class's OWL class subtree.
    if prefix_map:
        for cls_def in classes:
            root_iri = cls_def.get("root_iri")
            if not root_iri:
                continue
            class_curie = iri_to_curie(str(root_iri), prefix_map)
            if class_curie.startswith("<"):
                continue
            cls_name = cls_def["name"]
            parent_menu = (
                slot_range_overrides.get("parent", {}).get(cls_name)
                or global_slot_range.get("parent")
            )
            if parent_menu and parent_menu in enums:
                enums[parent_menu]["enum_uri"] = class_curie

    # --- classes ---
    _w(L, "classes:")
    _w(L)
    for cls_def in classes:
        cls_name = cls_def["name"]
        _w(L, f"  {cls_name}:")
        _w(L, f"    name: {cls_name}")
        _w(L, f"    title: {cls_name}")
        # class_uri: the CURIE for the OWL class this schema class represents.
        if cls_def.get("root_iri") and prefix_map:
            root_curie = iri_to_curie(str(cls_def["root_iri"]), prefix_map)
            if not root_curie.startswith("<"):
                _w(L, f"    class_uri: {root_curie}")
        if cls_def.get("description"):
            _w(L, "    description: >-")
            _w(L, f"      {cls_def['description'].strip()}")
        # Scope classes carry a slot_names list; main class gets all slots.
        # people_slots (set of snake names) restricts which people annotation
        # slots appear — only those with values in this class's OWL subtree.
        class_slot_names = cls_def.get("slot_names")
        people_slots = cls_def.get("people_slots")  # None → no restriction
        class_slots = [
            s for s in slots
            if (class_slot_names is None or to_snake(s["name"]) in set(class_slot_names))
            and (not s.get("people_menu") or people_slots is None
                 or to_snake(s["name"]) in people_slots)
        ]
        _w(L, "    slots:")
        for slot in class_slots:
            _w(L, f"      - {to_snake(slot['name'])}")
        _w(L, "    slot_usage:")
        for rank, slot in enumerate(class_slots, start=1):
            snake = to_snake(slot["name"])
            _w(L, f"      {snake}:")
            _w(L, f"        rank: {rank}")
            if slot.get("slot_group"):
                _w(L, f"        slot_group: {slot['slot_group']}")
            # Push-down: if this slot's range is overridden in any class,
            # write range explicitly for every class (override or global default).
            if snake in slot_range_overrides:
                range_val = (
                    slot_range_overrides[snake].get(cls_name)
                    or global_slot_range.get(snake)
                )
                if range_val:
                    _w(L, f"        range: {range_val}")

    # --- Container class (tree root for DataHarmonizer) ---
    # One multivalued attribute per data class; no foreign-key dependencies.
    _w(L)
    _w(L, "  Container:")
    _w(L, "    name: Container")
    _w(L, "    description: >-")
    _w(L, f"      Holds all {schema.get('name', 'ontology')} entity tables for DataHarmonizer.")
    _w(L, "    tree_root: true")
    _w(L, "    attributes:")
    for cls_def in classes:
        attr     = _plural_name(cls_def["name"])
        cls_name = cls_def["name"]
        _w(L, f"      {attr}:")
        _w(L, f"        name: {attr}")
        _w(L, f"        range: {cls_name}")
        _w(L, "        multivalued: true")
        _w(L, "        inlined_as_list: true")
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
            _w(L, f'      robot: "{robot}"')
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
        people_menu = slot.get("people_menu")
        # People slots: emit any_of with WhitespaceMinimizedString + menu enum.
        # Omit range from global slot when it has been pushed down per-class.
        if people_menu and enum_items.get(people_menu) is not None:
            _w(L, "    any_of:")
            _w(L, "      - range: WhitespaceMinimizedString")
            _w(L, f"      - range: {people_menu}")
        elif snake not in slot_range_overrides:
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
        if enum_def.get("enum_uri"):
            _w(L, f"    enum_uri: {enum_def['enum_uri']}")
        if enum_def.get("description"):
            _w(L, f"    description: {enum_def['description']}")
        _w(L, "    permissible_values:")
        # Detect duplicate labels within this enum; disambiguate by appending
        # the CURIE so each YAML key is unique.  The text/title fields keep the
        # original label so DataHarmonizer displays and round-trips it cleanly.
        label_counts: dict = {}
        for _, lbl, _, _ in items:
            label_counts[lbl] = label_counts.get(lbl, 0) + 1
        # Build curie→display-key map for this enum (used for is_a references).
        curie_to_key = {
            c: (f"{lbl} ({c})" if label_counts[lbl] > 1 else lbl)
            for c, lbl, _, _ in items
        }
        is_people = enum_def.get("people_menu", False)
        for curie, label, _iri, parent_curie in items:
            # Key is the rdfs:label (disambiguated with CURIE when needed);
            # text stays as the plain label so DH stores and saves it cleanly.
            # meaning carries the CURIE for downstream ROBOT/OWL use.
            # is_a encodes OWL subClassOf hierarchy for entries deeper than
            # one level from the enum root (direct children of root get no is_a).
            # People menus have no CURIEs/hierarchy; skip meaning and is_a.
            key = _yaml_scalar(curie_to_key[curie])
            lbl = _yaml_scalar(label)
            _w(L, f"      {key}:")
            _w(L, f"        text: {lbl}")
            _w(L, f"        title: {lbl}")
            if not is_people:
                _w(L, f"        meaning: {curie}")
            if not is_people and parent_curie and parent_curie in curie_to_key:
                parent_key = _yaml_scalar(curie_to_key[parent_curie])
                _w(L, f"        is_a: {parent_key}")
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
# TSV generation (--robot mode)
# ---------------------------------------------------------------------------

def build_label_map(g: Graph) -> dict:
    """Return {iri_string: label_string} for every entity with an rdfs:label."""
    result = {}
    for s, o in g.subject_objects(RDFS.label):
        if isinstance(s, URIRef) and isinstance(o, Literal):
            result[str(s)] = str(o)
    return result


def _curie_to_iri(curie: str, prefix_map: dict) -> str:
    """Resolve a CURIE (e.g. 'IAO:0000115') to a full IRI using prefix_map.
    Returns None when the prefix is unknown."""
    if ":" not in curie:
        return None
    prefix, local = curie.split(":", 1)
    ns = prefix_map.get(prefix)
    return (ns + local) if ns else None


def _plural_name(name: str) -> str:
    """Return a simple plural form for a CamelCase class name, used as the
    Container attribute key (e.g. Software→Softwares, DataEntity→DataEntities,
    DevelopmentStatus→DevelopmentStatuses)."""
    if name.endswith("Status"):
        return name + "es"
    if name.endswith("y") and len(name) > 1 and name[-2].lower() not in "aeiou":
        return name[:-1] + "ies"
    return name + "s"


def _build_people_menus(
    g: Graph, slots: list, prefix_map: dict
) -> tuple[dict, dict]:
    """Scan unique annotation values for all 'people slots' (those with a
    ``people_menu`` key) and return ``(enum_defs, enum_items)`` dicts.

    Each enum holds alphabetically sorted unique literal values found for the
    corresponding annotation property across the whole loaded graph.  Values are
    trimmed of whitespace.  Menus with zero entries are still created so the
    slot's ``any_of:`` block is written consistently.
    """
    enum_defs: dict = {}
    enum_items_out: dict = {}

    for slot in slots:
        menu_name = slot.get("people_menu")
        if not menu_name:
            continue
        slot_uri = slot.get("slot_uri")
        if not slot_uri:
            continue
        prop_iri = _curie_to_iri(slot_uri, prefix_map)
        if not prop_iri:
            continue
        prop = URIRef(prop_iri)

        values = sorted(
            {
                str(o).strip()
                for _, o in g.subject_objects(prop)
                if isinstance(o, Literal) and str(o).strip()
            },
            key=str.casefold,
        )

        enum_defs[menu_name] = {
            "description": f"Unique values for {slot_uri} found in the ontology.",
            "people_menu": True,
        }
        # Items: (value, value, None, None) — string-only, no CURIE or hierarchy.
        enum_items_out[menu_name] = [(v, v, None, None) for v in values]
        print(f"  {menu_name}: {len(values)} unique value(s)")

    return enum_defs, enum_items_out


def _assign_people_slots_to_classes(
    g: Graph, classes: list, all_slots: list, prefix_map: dict
) -> None:
    """For each class def, compute which people slots (those with a
    ``people_menu`` key) have at least one value anywhere in the class's OWL
    subtree (the root class itself plus all non-deprecated subclasses).

    The result is stored as ``cls_def['people_slots']`` — a set of snake-case
    slot names.  Slots NOT in this set are excluded from that class's column
    list in the generated schema.
    """
    people_slot_props: list = []   # [(snake_name, URIRef), ...]
    for slot in all_slots:
        if not slot.get("people_menu"):
            continue
        slot_uri = slot.get("slot_uri")
        if not slot_uri:
            continue
        prop_iri = _curie_to_iri(slot_uri, prefix_map)
        if not prop_iri:
            continue
        people_slot_props.append((to_snake(slot["name"]), URIRef(prop_iri)))

    if not people_slot_props:
        return

    for cls_def in classes:
        root_iri_str = cls_def.get("root_iri")
        if not root_iri_str:
            cls_def["people_slots"] = set()
            continue
        root_iri = URIRef(root_iri_str)
        subtree = all_subclasses(g, root_iri) | {root_iri}

        applicable: set = set()
        for snake, prop in people_slot_props:
            for cls_node in subtree:
                if next(g.objects(cls_node, prop), None) is not None:
                    applicable.add(snake)
                    break
        cls_def["people_slots"] = applicable


def _parse_robot_code(code: str) -> dict:
    """Parse a ROBOT template code string into a descriptor dict.

    Supported patterns:
      ID                                → {kind: 'id'}
      SC %                              → {kind: 'parent'}
      AL <curie>[@lang]                 → {kind: 'annotation', literal: True,
                                           prop_curie: ..., lang: ..., split: False}
      A  <curie>[@lang][ SPLIT=|]       → {kind: 'annotation', literal: False,
                                           prop_curie: ..., lang: ..., split: bool}
      SC 'prop label' some %[ SPLIT=|]  → {kind: 'some',  prop_label: ..., split: bool}
      SC 'prop label' value %[ SPLIT=|] → {kind: 'value', prop_label: ..., split: bool}

    Returns None for unrecognised / empty codes.
    """
    if not code:
        return None
    code = code.strip()

    if code == "ID":
        return {"kind": "id"}
    if code == "SC %":
        return {"kind": "parent"}

    # AL <curie>[@lang]
    m = re.match(r"^AL\s+(\S+?)(?:@(\w+))?$", code)
    if m:
        return {"kind": "annotation", "literal": True,
                "prop_curie": m.group(1), "lang": m.group(2), "split": False}

    # A <curie>[@lang][ SPLIT=|]
    m = re.match(r"^A\s+(\S+?)(?:@(\w+))?(?:\s+SPLIT=\|)?$", code)
    if m:
        return {"kind": "annotation", "literal": False,
                "prop_curie": m.group(1), "lang": m.group(2),
                "split": "SPLIT=|" in code}

    # SC 'prop label' some/value %[ SPLIT=|]
    m = re.match(r"^SC\s+'(.+?)'\s+(some|value)\s+%(\s+SPLIT=\|)?$", code)
    if m:
        return {"kind": m.group(2), "prop_label": m.group(1),
                "split": bool(m.group(3))}

    return None


def _extract_tsv_row(
    g: Graph,
    cls: URIRef,
    slot_descriptors: list,
    labels: dict,
    prefix_map: dict,
    prop_label_map: dict,
) -> list:
    """Return a list of cell values (strings) for one class row.

    slot_descriptors: list of (robot_code, parsed_robot_dict) in column order.
    prop_label_map: {rdfs_label_str: iri_str} for all properties in the graph.
    """
    row = []
    for robot_code, parsed in slot_descriptors:
        if parsed is None:
            row.append("")
            continue

        kind = parsed["kind"]

        # --- ID ---
        if kind == "id":
            row.append(iri_to_curie(str(cls), prefix_map))

        # --- Parent (SC %) ---
        elif kind == "parent":
            val = ""
            for parent in g.objects(cls, RDFS.subClassOf):
                if isinstance(parent, URIRef) and not is_deprecated(g, parent):
                    val = labels.get(str(parent)) or iri_to_curie(str(parent), prefix_map)
                    break
            row.append(val)

        # --- Annotation property ---
        elif kind == "annotation":
            prop_iri_str = _curie_to_iri(parsed["prop_curie"], prefix_map)
            if not prop_iri_str:
                row.append("")
                continue
            prop = URIRef(prop_iri_str)
            lang = parsed.get("lang")
            split = parsed.get("split", False)
            values = []
            for obj in g.objects(cls, prop):
                if isinstance(obj, Literal):
                    if lang is None or str(obj.language) == lang:
                        # Normalise embedded newlines — TSV cells must be single-line
                        values.append(" ".join(str(obj).splitlines()))
                elif isinstance(obj, URIRef):
                    values.append(str(obj))
            if not values:
                row.append("")
            elif split:
                row.append("|".join(values))
            else:
                row.append(values[0])

        # --- someValuesFrom or hasValue restriction ---
        elif kind in ("some", "value"):
            prop_lbl   = parsed["prop_label"]
            prop_iri_str = prop_label_map.get(prop_lbl)
            if not prop_iri_str:
                row.append("")
                continue
            prop  = URIRef(prop_iri_str)
            split = parsed.get("split", False)
            values = []
            for sc in g.objects(cls, RDFS.subClassOf):
                if not isinstance(sc, BNode):
                    continue
                if (sc, RDF.type, OWL.Restriction) not in g:
                    continue
                if g.value(sc, OWL.onProperty) != prop:
                    continue
                if kind == "some":
                    filler = g.value(sc, OWL.someValuesFrom)
                    # Only named-class fillers; skip BNode intersections
                    # (those belonged to the now-omitted 'datasets' slots).
                    if isinstance(filler, URIRef):
                        lbl = labels.get(str(filler)) or iri_to_curie(str(filler), prefix_map)
                        values.append(lbl)
                else:  # value
                    val = g.value(sc, OWL.hasValue)
                    if isinstance(val, URIRef):
                        lbl = labels.get(str(val)) or iri_to_curie(str(val), prefix_map)
                        values.append(lbl)
                    elif isinstance(val, Literal):
                        # Literal hasValue fillers (URLs, dates) must be quoted in
                        # ROBOT template syntax so they are treated as string literals,
                        # not parsed as Manchester IRI expressions.
                        values.append(f'"{val}"')
            if not values:
                row.append("")
            elif split:
                row.append("|".join(values))
            else:
                row.append(values[0])

        else:
            row.append("")

    return row


def write_class_tsv(
    g: Graph,
    schema: dict,
    class_name: str,
    prefix_map: dict,
    output_dir: str,
    file_prefix: str = "",
) -> None:
    """Write a ROBOT template TSV for the named schema class.

    Finds the class definition in *schema*, resolves its root OWL class by
    matching the rdfs:label of the given *class_name* (case-insensitive),
    iterates over its direct non-deprecated subclasses, and writes one row
    per subclass to <output_dir>/<file_prefix>_<ClassName>.tsv.
    """
    classes = schema.get("classes", {})
    # Case-insensitive class lookup
    cls_key = next(
        (k for k in classes if k.lower() == class_name.lower()), None
    )
    if cls_key is None:
        print(f"ERROR: class '{class_name}' not found in schema (available: "
              f"{', '.join(classes)}).", file=sys.stderr)
        return

    cls_def    = classes[cls_key]
    all_slots  = schema.get("slots", {})
    slot_usage = cls_def.get("slot_usage", {})

    # Build ordered slot list from class definition, sorted by rank
    slot_names = cls_def.get("slots", [])
    ranked = sorted(
        slot_names,
        key=lambda s: slot_usage.get(to_snake(s), {}).get("rank", 9999),
    )

    # Build column descriptors: (title, robot_code, parsed_robot)
    columns = []
    for sn in ranked:
        snake    = to_snake(sn)
        slot_def = all_slots.get(snake, {})
        title    = slot_def.get("title", snake)
        robot    = slot_def.get("annotations", {}).get("robot", "")
        parsed   = _parse_robot_code(robot)
        columns.append((title, robot, parsed))

    # Find root OWL class.
    # Prefer class_uri written into the schema (reliable for scope classes
    # whose CamelCase name may not match their OWL rdfs:label directly).
    # Fall back to two label-matching normalisations for backward compat:
    #   1. exact case-insensitive match  ("Licence" → "licence")
    #   2. CamelCase split + lower       ("DataFormatSpecification" →
    #                                     "data format specification")
    class_uri_curie = cls_def.get("class_uri")
    if class_uri_curie:
        # Convert CURIE back to full IRI using the prefix map
        full_iri = _curie_to_iri(class_uri_curie, prefix_map)
        root_iri = URIRef(full_iri) if full_iri else None
    else:
        name_exact = class_name.lower()
        name_split = re.sub(r"([a-z])([A-Z])", r"\1 \2", class_name).lower()
        root_iri   = None
        for s, o in g.subject_objects(RDFS.label):
            if isinstance(s, URIRef) and isinstance(o, Literal):
                lbl = str(o).lower()
                if lbl == name_exact or lbl == name_split:
                    root_iri = s
                    break
    if root_iri is None:
        print(f"ERROR: no OWL class with rdfs:label matching '{class_name}'.",
              file=sys.stderr)
        return

    labels         = build_label_map(g)
    prop_label_map = {str(o): str(s)
                      for s, o in g.subject_objects(RDFS.label)
                      if isinstance(s, URIRef) and isinstance(o, Literal)}

    # Hierarchical DFS order: children sorted alphabetically appear immediately
    # after their parent, mirroring the Protégé class tree display.
    def _tree_order(parent: URIRef, seen: set) -> list:
        children = sorted(
            [c for c in g.subjects(RDFS.subClassOf, parent)
             if isinstance(c, URIRef) and not is_deprecated(g, c) and c not in seen],
            key=lambda c: labels.get(str(c), str(c)).casefold(),
        )
        result = []
        for child in children:
            seen.add(child)
            result.append(child)
            result.extend(_tree_order(child, seen))
        return result

    subclasses = _tree_order(root_iri, set())

    slot_descriptors = [(robot, parsed) for _, robot, parsed in columns]
    header_row       = [title for title, _, _ in columns]
    robot_row        = [robot for _, robot, _ in columns]

    tsv_name = f"{file_prefix}_{class_name}.tsv" if file_prefix else f"{class_name}.tsv"
    tsv_path = os.path.join(output_dir, tsv_name)
    os.makedirs(output_dir, exist_ok=True)
    with open(tsv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(header_row)
        writer.writerow(robot_row)
        for cls in subclasses:
            writer.writerow(_extract_tsv_row(
                g, cls, slot_descriptors, labels, prefix_map, prop_label_map
            ))

    print(f"Wrote {len(subclasses)} rows to {tsv_path}")


# ---------------------------------------------------------------------------
# JSON data generation
# ---------------------------------------------------------------------------

def write_schema_json(
    g: Graph,
    spec: dict,
    prefix_map: dict,
    output_path: str,
) -> None:
    """Write schema_data.json in DataHarmonizer Container format.

    Extracts OWL class data for every schema class (excluding Container) and
    assembles it into a JSON container keyed by the plural attribute name
    (e.g. ``Softwares``, ``DataEntities``), mirroring the format DataHarmonizer
    uses when saving data.  Empty field values are omitted; multivalued slots
    with more than one value are stored as JSON arrays.
    """
    s_meta    = spec.get("schema", {})
    classes   = spec.get("classes", [])
    all_slots = spec.get("slots", [])

    labels         = build_label_map(g)
    prop_label_map = {
        str(o): str(s)
        for s, o in g.subject_objects(RDFS.label)
        if isinstance(s, URIRef) and isinstance(o, Literal)
    }
    slots_by_snake = {to_snake(sl["name"]): sl for sl in all_slots}

    def _slot_descs(cls_def):
        """Return [(snake_name, parsed_robot)] in rank order for cls_def.

        In-memory spec dicts use ``slot_names`` (a filtered list of snake names)
        for scope classes, or no such key for the main class (all slots apply).
        ``slot_usage`` is not stored in the in-memory spec so all ranks are 9999
        and slots come out in their declaration order, which is fine for JSON.
        """
        slot_name_filter = cls_def.get("slot_names")  # None → all slots
        applicable = [
            sl for sl in all_slots
            if slot_name_filter is None or to_snake(sl["name"]) in set(slot_name_filter)
        ]
        result = []
        for sl in applicable:
            snake = to_snake(sl["name"])
            # In the in-memory spec, the robot code lives directly on the slot dict.
            # (In the parsed schema YAML it is nested under annotations.robot.)
            robot = sl.get("robot", "") or sl.get("annotations", {}).get("robot", "")
            result.append((snake, _parse_robot_code(robot)))
        return result

    def _json_row(cls, descs):
        """Return a non-empty {slot_name: value} dict for one OWL class."""
        row = {}
        for snake, parsed in descs:
            if parsed is None:
                continue
            kind = parsed["kind"]

            if kind == "id":
                v = iri_to_curie(str(cls), prefix_map)
                if v and not v.startswith("<"):
                    row[snake] = v

            elif kind == "parent":
                for par in g.objects(cls, RDFS.subClassOf):
                    if isinstance(par, URIRef) and not is_deprecated(g, par):
                        lbl = (labels.get(str(par)) or iri_to_curie(str(par), prefix_map) or "").strip()
                        if lbl:
                            row[snake] = lbl
                        break

            elif kind == "annotation":
                prop_iri = _curie_to_iri(parsed["prop_curie"], prefix_map)
                if not prop_iri:
                    continue
                prop  = URIRef(prop_iri)
                lang  = parsed.get("lang")
                split = parsed.get("split", False)
                vals  = []
                for obj in g.objects(cls, prop):
                    if isinstance(obj, Literal):
                        if lang is None or str(obj.language) == lang:
                            vals.append(" ".join(str(obj).splitlines()))
                    elif isinstance(obj, URIRef):
                        vals.append(str(obj))
                vals = [v.strip() for v in vals if v.strip()]
                if vals:
                    if split:
                        row[snake] = vals   # always a list for multivalued slots
                    else:
                        row[snake] = vals[0]

            elif kind in ("some", "value"):
                prop_iri = prop_label_map.get(parsed["prop_label"])
                if not prop_iri:
                    continue
                prop  = URIRef(prop_iri)
                split = parsed.get("split", False)
                vals  = []
                for sc in g.objects(cls, RDFS.subClassOf):
                    if not isinstance(sc, BNode):
                        continue
                    if (sc, RDF.type, OWL.Restriction) not in g:
                        continue
                    if g.value(sc, OWL.onProperty) != prop:
                        continue
                    if kind == "some":
                        filler = g.value(sc, OWL.someValuesFrom)
                        if isinstance(filler, URIRef):
                            vals.append(
                                labels.get(str(filler))
                                or iri_to_curie(str(filler), prefix_map)
                            )
                    else:
                        val = g.value(sc, OWL.hasValue)
                        if isinstance(val, URIRef):
                            vals.append(
                                labels.get(str(val))
                                or iri_to_curie(str(val), prefix_map)
                            )
                        elif isinstance(val, Literal):
                            vals.append(str(val))
                vals = [v.strip() for v in vals if v.strip()]
                if vals:
                    if split:
                        row[snake] = vals   # always a list for multivalued slots
                    else:
                        row[snake] = vals[0]

        return row

    def _dfs_subclasses(root):
        def _walk(parent, seen):
            children = sorted(
                [c for c in g.subjects(RDFS.subClassOf, parent)
                 if isinstance(c, URIRef) and not is_deprecated(g, c) and c not in seen],
                key=lambda c: labels.get(str(c), str(c)).casefold(),
            )
            result = []
            for child in children:
                seen.add(child)
                result.append(child)
                result.extend(_walk(child, seen))
            return result
        return _walk(root, set())

    container = {}
    for cls_def in classes:
        cls_name = cls_def["name"]
        if cls_name == "Container":
            continue

        root_iri_str = cls_def.get("root_iri")
        if not root_iri_str:
            continue
        root_iri = URIRef(root_iri_str)

        descs   = _slot_descs(cls_def)
        members = _dfs_subclasses(root_iri)
        rows    = [r for m in members if (r := _json_row(m, descs))]

        attr = _plural_name(cls_name)
        container[attr] = rows
        print(f"  {cls_name}: {len(rows)} rows → {attr}")

    output = {
        "schema":      s_meta.get("id", ""),
        "version":     s_meta.get("version", "1.0.0"),
        "in_language": "en",
        "container":   container,
    }

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
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
    parser.add_argument(
        "--robot", metavar="CLASS", dest="robot_classes", nargs="+",
        help=(
            "Instead of generating schema.yaml, generate a ROBOT template TSV "
            "for each named schema class (e.g. --robot software).  Reads the "
            "schema.yaml at --output to determine columns; writes <class>.tsv "
            "in the same directory.  Requires --ontology and an existing "
            "schema.yaml."
        ),
    )
    parser.add_argument(
        "--scope", metavar="INT", type=int, default=0,
        help=(
            "0 (default): generate schema for the root class only. "
            "1: also generate a class and TSV tab for every enum that maps to "
            "a single OWL parent class (object-property targets such as "
            "'programming language', 'license', etc.)."
        ),
    )
    args = parser.parse_args()

    # --- Validate mode ---
    robot_mode = bool(getattr(args, "robot_classes", None))
    if not args.spec and not args.root_class and not robot_mode:
        parser.error(
            "Provide --spec (spec-driven mode), --class IRI (OWL-driven mode), "
            "or --robot CLASS (TSV generation mode)."
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
        _load_owl_to_graph(g, owl_path)
        for extra in args.also_load:
            extra_path = os.path.abspath(extra)
            print(f"  also loading {extra_path} ...")
            _load_owl_to_graph(g, extra_path)
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
        write_schema_yaml(spec, enum_items, output_path, prefix_map=prefix_map)
        json_path = os.path.splitext(output_path)[0] + "_data.json"
        print(f"\nWriting {json_path} ...")
        write_schema_json(g, spec, prefix_map, json_path)
        print("Done.")
        return

    # -----------------------------------------------------------------------
    # OWL-driven mode
    # -----------------------------------------------------------------------
    if not args.ontology:
        parser.error("--ontology is required in OWL-driven mode.")

    owl_path = os.path.abspath(args.ontology)
    owl_dir  = os.path.dirname(owl_path)

    # Load ontology (and any additional component files) first so we can build
    # the prefix map, which is needed to derive the correct file prefix below.
    print(f"Loading {owl_path} ...")
    g = Graph()
    _load_owl_to_graph(g, owl_path)
    for extra in args.also_load:
        extra_path = os.path.abspath(extra)
        print(f"  also loading {extra_path} ...")
        _load_owl_to_graph(g, extra_path)
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

    # Derive the file prefix from the ontology's default_prefix (the CURIE
    # namespace of the root class), matching the value that build_schema_from_owl
    # stores as default_prefix in the schema.  Falls back to the filename-derived
    # prefix (uppercased per OBO convention) when the IRI can't be resolved.
    # TODO: allow schema_config.yaml to supply a "file_prefix:" override.
    _fb = _derive_ontology_prefix(owl_path)   # filename fallback, e.g. "SWO"
    _root_curie = iri_to_curie(args.root_class, prefix_map) if args.root_class else ""
    file_prefix = (
        getattr(args, "default_prefix", None)
        or (_root_curie.split(":")[0]
            if ":" in _root_curie and not _root_curie.startswith("<")
            else _fb)
    )

    # Resolve output and config paths using the ontology's default_prefix
    output_path = os.path.abspath(
        args.output if args.output
        else os.path.join(owl_dir, f"{file_prefix}_schema.yaml")
    )
    config_path = os.path.abspath(
        args.config_path if args.config_path
        else os.path.join(
            os.path.dirname(output_path),
            f"{file_prefix}_schema_config.yaml",
        )
    )

    # Load existing enum decisions
    config = load_schema_config(config_path)
    if os.path.exists(config_path):
        print(f"Config loaded from {config_path}")

    # --robot mode: generate TSV file(s) from an existing schema.yaml
    if getattr(args, "robot_classes", None):
        if not os.path.exists(output_path):
            print(
                f"ERROR: schema.yaml not found at {output_path}.\n"
                f"Run without --robot first to generate it.",
                file=sys.stderr,
            )
            sys.exit(1)
        with open(output_path, encoding="utf-8") as fh:
            schema = yaml.safe_load(fh)
        # Use schema.yaml's own prefixes section — no separate --prefixes needed.
        schema_prefixes = schema.get("prefixes", {})
        if schema_prefixes:
            prefix_map = {k.rstrip(":"): v for k, v in schema_prefixes.items()}
        tsv_prefix = schema.get("default_prefix") or file_prefix
        output_dir = os.path.dirname(output_path)
        for class_name in args.robot_classes:
            write_class_tsv(g, schema, class_name, prefix_map, output_dir, tsv_prefix)
        return

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

    # Enrich enums with enum_uri (single-root enums only; always applied)
    _enrich_enum_uris(g, spec, prefix_map)

    # Apply any manually-specified enum_uri from schema_config.yaml, overriding
    # the graph-derived value so curators can pin the URI for enums whose
    # rdfs:range is absent or points to a deprecated class.
    for enum_name, enum_def in spec.get("enums", {}).items():
        cfg_uri = config.get("enums", {}).get(enum_name, {}).get("enum_uri")
        if cfg_uri:
            enum_def["enum_uri"] = cfg_uri

    # --scope 1: add a class and menu enum for each single-root enum target
    if getattr(args, "scope", 0) >= 1:
        scope_classes, scope_enums, scope_enum_items = _build_scope_classes(
            g, spec, enum_items, prefix_map
        )
        spec["classes"].extend(scope_classes)
        spec["enums"].update(scope_enums)
        enum_items.update(scope_enum_items)
        if scope_classes:
            print(
                f"\nScope 1: added {len(scope_classes)} class(es): "
                + ", ".join(c["name"] for c in scope_classes)
            )

    # Build people menus from unique annotation values in the loaded graph.
    # (dcterms:creator, dc:contributor, dcterms:contributor, IAO:0000117)
    print("\nBuilding people menus ...")
    people_enum_defs, people_enum_items = _build_people_menus(
        g, spec["slots"], prefix_map
    )
    spec["enums"].update(people_enum_defs)
    enum_items.update(people_enum_items)

    # Determine per-class which people slots are applicable (have values in
    # that class's OWL subtree); store as cls_def['people_slots'].
    _assign_people_slots_to_classes(g, spec["classes"], spec["slots"], prefix_map)

    # Write output
    print(f"\nWriting {output_path} ...")
    write_schema_yaml(spec, enum_items, output_path, prefix_map=prefix_map)
    json_path = os.path.splitext(output_path)[0] + "_data.json"
    print(f"\nWriting {json_path} ...")
    write_schema_json(g, spec, prefix_map, json_path)
    print("Done.")


if __name__ == "__main__":
    main()
