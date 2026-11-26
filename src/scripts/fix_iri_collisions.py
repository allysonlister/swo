#!/usr/bin/env python3
"""
fix_iri_collisions.py
---------------------
Resolves IRI namespace collisions in swo-edit.owl by:
  1. Minting new SWO: terms to replace conflicting sub-namespace terms
  2. Deprecating old terms (owl:deprecated, renamed label, IAO:0100001 term_replaced_by)
  3. Updating all axiom references from deprecated IRIs to new IRIs
  4. Extracting ALL deprecated terms (pre-existing + new) to imports/deprecated.owl
  5. Adding owl:imports for deprecated.owl in swo-edit.owl
  6. Adding the catalog entry for deprecated.owl

Usage:
    cd src/ontology
    python ../scripts/fix_iri_collisions.py
"""

import os
import re
import shutil
import datetime
from rdflib import Graph, URIRef, Literal, BNode, Namespace, RDF, RDFS, OWL
from rdflib.namespace import XSD

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
ONT_DIR      = os.path.join(SCRIPT_DIR, "..", "ontology")
SWO_OWL      = os.path.join(ONT_DIR, "swo-edit.owl")
SW_OWL       = os.path.join(ONT_DIR, "components", "swo-software.owl")
DEP_OWL      = os.path.join(ONT_DIR, "imports", "deprecated.owl")
CATALOG      = os.path.join(ONT_DIR, "catalog-v001.xml")

SWO_BASE      = "http://www.ebi.ac.uk/swo/SWO_"
OBO_BASE      = "http://purl.obolibrary.org/obo/SWO_"   # target for SWO_XXXXXXX terms
EBI_SWO_BASE  = "http://www.ebi.ac.uk/swo/"
OBO_SWO_BASE  = "http://purl.obolibrary.org/obo/swo/"   # target for non-SWO_XXXXXXX terms

# IRIs that look like EBI SWO but must NOT be migrated (deprecated terms without replacements)
_SWO_SKIP = {
    "http://www.ebi.ac.uk/swo/ontology",                    # deprecated, no replacement
    "http://www.ebi.ac.uk/swo/software_before_Microsoft_2007",  # deprecated, no replacement
}

# IRIs requiring a special explicit mapping (suffix doesn't follow EBI→OBO_SWO pattern)
_SWO_DIRECT_MAP = {
    "http://www.ebi.ac.uk/swo/swo.owl": "http://purl.obolibrary.org/obo/swo.owl",
}

# Regex matching ANY SWO term IRI (main namespace or any sub-namespace):
#   http://www.ebi.ac.uk/swo/SWO_{num}
#   http://www.ebi.ac.uk/swo/{subns}/SWO_{num}
# Capture group 1 is the numeric suffix.
_SWO_TERM_RE  = re.compile(
    r"http://www\.ebi\.ac\.uk/swo/(?:\w+/)?SWO_(\d+)$"
)
DEP_ONT_IRI   = "http://purl.obolibrary.org/obo/swo/imports/deprecated.owl"
GITHUB_ISSUE  = "https://github.com/allysonlister/swo/issues/61"  # collision-fix issue

IAO     = Namespace("http://purl.obolibrary.org/obo/IAO_")
DC      = Namespace("http://purl.org/dc/elements/1.1/")
EFO     = Namespace("http://www.ebi.ac.uk/efo/")

TERM_REPLACED_BY  = IAO["0100001"]                  # IAO:0100001 term replaced by
TERM_TRACKER      = IAO["0000233"]                  # IAO:0000233 term tracker item
OWL_DEP           = OWL.deprecated
OBS_IN_VERSION    = EFO["obsoleted_in_version"]     # efo:obsoleted_in_version
DC_CONTRIBUTOR    = DC["contributor"]               # dc:contributor

NEW_OBS_VERSION   = "1.9"
NEW_OBS_CONTRIB   = "Marked as obsolete by Damion Dooley."

# ---------------------------------------------------------------------------
# Hardcoded collision assignments
# (num, p_iri, c_iri, new1_iri, new1_note, new2_iri)
# new1_note: "retained" | "same-id" | "minted"
# ---------------------------------------------------------------------------
ASSIGNMENTS = [
    # --- SWOB: vs SWOA:algorithm/ (6 rows) ---
    ("4000004",
     "http://www.ebi.ac.uk/swo/objective/SWO_4000004",
     "http://www.ebi.ac.uk/swo/algorithm/SWO_4000004",
     "http://www.ebi.ac.uk/swo/SWO_4000004",   "same-id",
     "http://www.ebi.ac.uk/swo/SWO_0000750"),
    ("4000005",
     "http://www.ebi.ac.uk/swo/objective/SWO_4000005",
     "http://www.ebi.ac.uk/swo/algorithm/SWO_4000005",
     "http://www.ebi.ac.uk/swo/SWO_4000005",   "same-id",
     "http://www.ebi.ac.uk/swo/SWO_0000751"),
    ("4000006",
     "http://www.ebi.ac.uk/swo/objective/SWO_4000006",
     "http://www.ebi.ac.uk/swo/algorithm/SWO_4000006",
     "http://www.ebi.ac.uk/swo/SWO_4000006",   "same-id",
     "http://www.ebi.ac.uk/swo/SWO_0000752"),
    ("4000008",
     "http://www.ebi.ac.uk/swo/objective/SWO_4000008",
     "http://www.ebi.ac.uk/swo/algorithm/SWO_4000008",
     "http://www.ebi.ac.uk/swo/SWO_4000008",   "same-id",
     "http://www.ebi.ac.uk/swo/SWO_0000753"),
    ("4000009",
     "http://www.ebi.ac.uk/swo/objective/SWO_4000009",
     "http://www.ebi.ac.uk/swo/algorithm/SWO_4000009",
     "http://www.ebi.ac.uk/swo/SWO_4000009",   "same-id",
     "http://www.ebi.ac.uk/swo/SWO_0000754"),
    ("4000010",
     "http://www.ebi.ac.uk/swo/objective/SWO_4000010",
     "http://www.ebi.ac.uk/swo/algorithm/SWO_4000010",
     "http://www.ebi.ac.uk/swo/SWO_4000010",   "same-id",
     "http://www.ebi.ac.uk/swo/SWO_0000755"),

    # --- SWO: (retained) vs SWOO: (25 rows) ---
    ("9000005","http://www.ebi.ac.uk/swo/SWO_9000005","http://www.ebi.ac.uk/swo/organization/SWO_9000005","http://www.ebi.ac.uk/swo/SWO_9000005","retained","http://www.ebi.ac.uk/swo/SWO_0000756"),
    ("9000006","http://www.ebi.ac.uk/swo/SWO_9000006","http://www.ebi.ac.uk/swo/organization/SWO_9000006","http://www.ebi.ac.uk/swo/SWO_9000006","retained","http://www.ebi.ac.uk/swo/SWO_0000757"),
    ("9000007","http://www.ebi.ac.uk/swo/SWO_9000007","http://www.ebi.ac.uk/swo/organization/SWO_9000007","http://www.ebi.ac.uk/swo/SWO_9000007","retained","http://www.ebi.ac.uk/swo/SWO_0000758"),
    ("9000008","http://www.ebi.ac.uk/swo/SWO_9000008","http://www.ebi.ac.uk/swo/organization/SWO_9000008","http://www.ebi.ac.uk/swo/SWO_9000008","retained","http://www.ebi.ac.uk/swo/SWO_0000759"),
    ("9000009","http://www.ebi.ac.uk/swo/SWO_9000009","http://www.ebi.ac.uk/swo/organization/SWO_9000009","http://www.ebi.ac.uk/swo/SWO_9000009","retained","http://www.ebi.ac.uk/swo/SWO_0000760"),
    ("9000010","http://www.ebi.ac.uk/swo/SWO_9000010","http://www.ebi.ac.uk/swo/organization/SWO_9000010","http://www.ebi.ac.uk/swo/SWO_9000010","retained","http://www.ebi.ac.uk/swo/SWO_0000761"),
    ("9000011","http://www.ebi.ac.uk/swo/SWO_9000011","http://www.ebi.ac.uk/swo/organization/SWO_9000011","http://www.ebi.ac.uk/swo/SWO_9000011","retained","http://www.ebi.ac.uk/swo/SWO_0000762"),
    ("9000012","http://www.ebi.ac.uk/swo/SWO_9000012","http://www.ebi.ac.uk/swo/organization/SWO_9000012","http://www.ebi.ac.uk/swo/SWO_9000012","retained","http://www.ebi.ac.uk/swo/SWO_0000763"),
    ("9000013","http://www.ebi.ac.uk/swo/SWO_9000013","http://www.ebi.ac.uk/swo/organization/SWO_9000013","http://www.ebi.ac.uk/swo/SWO_9000013","retained","http://www.ebi.ac.uk/swo/SWO_0000764"),
    ("9000016","http://www.ebi.ac.uk/swo/SWO_9000016","http://www.ebi.ac.uk/swo/organization/SWO_9000016","http://www.ebi.ac.uk/swo/SWO_9000016","retained","http://www.ebi.ac.uk/swo/SWO_0000765"),
    ("9000017","http://www.ebi.ac.uk/swo/SWO_9000017","http://www.ebi.ac.uk/swo/organization/SWO_9000017","http://www.ebi.ac.uk/swo/SWO_9000017","retained","http://www.ebi.ac.uk/swo/SWO_0000766"),
    ("9000018","http://www.ebi.ac.uk/swo/SWO_9000018","http://www.ebi.ac.uk/swo/organization/SWO_9000018","http://www.ebi.ac.uk/swo/SWO_9000018","retained","http://www.ebi.ac.uk/swo/SWO_0000767"),
    ("9000019","http://www.ebi.ac.uk/swo/SWO_9000019","http://www.ebi.ac.uk/swo/organization/SWO_9000019","http://www.ebi.ac.uk/swo/SWO_9000019","retained","http://www.ebi.ac.uk/swo/SWO_0000768"),
    ("9000020","http://www.ebi.ac.uk/swo/SWO_9000020","http://www.ebi.ac.uk/swo/organization/SWO_9000020","http://www.ebi.ac.uk/swo/SWO_9000020","retained","http://www.ebi.ac.uk/swo/SWO_0000769"),
    ("9000021","http://www.ebi.ac.uk/swo/SWO_9000021","http://www.ebi.ac.uk/swo/organization/SWO_9000021","http://www.ebi.ac.uk/swo/SWO_9000021","retained","http://www.ebi.ac.uk/swo/SWO_0000770"),
    ("9000022","http://www.ebi.ac.uk/swo/SWO_9000022","http://www.ebi.ac.uk/swo/organization/SWO_9000022","http://www.ebi.ac.uk/swo/SWO_9000022","retained","http://www.ebi.ac.uk/swo/SWO_0000771"),
    ("9000023","http://www.ebi.ac.uk/swo/SWO_9000023","http://www.ebi.ac.uk/swo/organization/SWO_9000023","http://www.ebi.ac.uk/swo/SWO_9000023","retained","http://www.ebi.ac.uk/swo/SWO_0000772"),
    ("9000024","http://www.ebi.ac.uk/swo/SWO_9000024","http://www.ebi.ac.uk/swo/organization/SWO_9000024","http://www.ebi.ac.uk/swo/SWO_9000024","retained","http://www.ebi.ac.uk/swo/SWO_0000773"),
    ("9000025","http://www.ebi.ac.uk/swo/SWO_9000025","http://www.ebi.ac.uk/swo/organization/SWO_9000025","http://www.ebi.ac.uk/swo/SWO_9000025","retained","http://www.ebi.ac.uk/swo/SWO_0000774"),
    ("9000026","http://www.ebi.ac.uk/swo/SWO_9000026","http://www.ebi.ac.uk/swo/organization/SWO_9000026","http://www.ebi.ac.uk/swo/SWO_9000026","retained","http://www.ebi.ac.uk/swo/SWO_0000775"),
    ("9000027","http://www.ebi.ac.uk/swo/SWO_9000027","http://www.ebi.ac.uk/swo/organization/SWO_9000027","http://www.ebi.ac.uk/swo/SWO_9000027","retained","http://www.ebi.ac.uk/swo/SWO_0000776"),
    ("9000028","http://www.ebi.ac.uk/swo/SWO_9000028","http://www.ebi.ac.uk/swo/organization/SWO_9000028","http://www.ebi.ac.uk/swo/SWO_9000028","retained","http://www.ebi.ac.uk/swo/SWO_0000777"),
    ("9000029","http://www.ebi.ac.uk/swo/SWO_9000029","http://www.ebi.ac.uk/swo/organization/SWO_9000029","http://www.ebi.ac.uk/swo/SWO_9000029","retained","http://www.ebi.ac.uk/swo/SWO_0000778"),

    # --- SWOO: vs SWOA:maturity/ (5 rows) ---
    ("9000061","http://www.ebi.ac.uk/swo/organization/SWO_9000061","http://www.ebi.ac.uk/swo/maturity/SWO_9000061","http://www.ebi.ac.uk/swo/SWO_9000061","same-id","http://www.ebi.ac.uk/swo/SWO_0000779"),
    ("9000062","http://www.ebi.ac.uk/swo/organization/SWO_9000062","http://www.ebi.ac.uk/swo/maturity/SWO_9000062","http://www.ebi.ac.uk/swo/SWO_9000062","same-id","http://www.ebi.ac.uk/swo/SWO_0000780"),
    ("9000063","http://www.ebi.ac.uk/swo/organization/SWO_9000063","http://www.ebi.ac.uk/swo/maturity/SWO_9000063","http://www.ebi.ac.uk/swo/SWO_9000063","same-id","http://www.ebi.ac.uk/swo/SWO_0000781"),
    ("9000064","http://www.ebi.ac.uk/swo/organization/SWO_9000064","http://www.ebi.ac.uk/swo/maturity/SWO_9000064","http://www.ebi.ac.uk/swo/SWO_9000064","same-id","http://www.ebi.ac.uk/swo/SWO_0000782"),
    ("9000065","http://www.ebi.ac.uk/swo/organization/SWO_9000065","http://www.ebi.ac.uk/swo/maturity/SWO_9000065","http://www.ebi.ac.uk/swo/SWO_9000065","same-id","http://www.ebi.ac.uk/swo/SWO_0000783"),

    # --- SWO: (retained) vs SWOA:maturity/ (4 rows) ---
    ("9000070","http://www.ebi.ac.uk/swo/SWO_9000070","http://www.ebi.ac.uk/swo/maturity/SWO_9000070","http://www.ebi.ac.uk/swo/SWO_9000070","retained","http://www.ebi.ac.uk/swo/SWO_0000784"),
    ("9000071","http://www.ebi.ac.uk/swo/SWO_9000071","http://www.ebi.ac.uk/swo/maturity/SWO_9000071","http://www.ebi.ac.uk/swo/SWO_9000071","retained","http://www.ebi.ac.uk/swo/SWO_0000785"),
    ("9000072","http://www.ebi.ac.uk/swo/SWO_9000072","http://www.ebi.ac.uk/swo/maturity/SWO_9000072","http://www.ebi.ac.uk/swo/SWO_9000072","retained","http://www.ebi.ac.uk/swo/SWO_0000786"),
    ("9000073","http://www.ebi.ac.uk/swo/SWO_9000073","http://www.ebi.ac.uk/swo/maturity/SWO_9000073","http://www.ebi.ac.uk/swo/SWO_9000073","retained","http://www.ebi.ac.uk/swo/SWO_0000787"),

    # --- SWO: (retained) vs SWOL: (1 row) ---
    ("9000074","http://www.ebi.ac.uk/swo/SWO_9000074","http://www.ebi.ac.uk/swo/license/SWO_9000074","http://www.ebi.ac.uk/swo/SWO_9000074","retained","http://www.ebi.ac.uk/swo/SWO_0000788"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def deep_copy_bnode(g, bnode, bnode_map):
    """Return a new BNode that is an independent deep copy of bnode in graph g."""
    if bnode in bnode_map:
        return bnode_map[bnode]
    new_b = BNode()
    bnode_map[bnode] = new_b
    for p, o in g.predicate_objects(bnode):
        new_o = deep_copy_bnode(g, o, bnode_map) if isinstance(o, BNode) else o
        g.add((new_b, p, new_o))
    return new_b


def copy_term(g, old_uri, new_uri):
    """Add all triples for old_uri to graph with new_uri as subject.
    Blank node objects are deep-copied so the new term is fully independent."""
    bnode_map = {}
    for p, o in list(g.predicate_objects(old_uri)):
        new_o = deep_copy_bnode(g, o, bnode_map) if isinstance(o, BNode) else o
        g.add((new_uri, p, new_o))


def update_object_references(g, old_uri, new_uri):
    """Replace every occurrence of old_uri as an object in any triple (including
    blank-node subjects such as restriction nodes)."""
    hits = [(s, p) for s, p, o in g.triples((None, None, old_uri))]
    for s, p in hits:
        g.remove((s, p, old_uri))
        g.add((s, p, new_uri))


def deprecate_term(g, old_uri, new_uri, comment):
    """Mark old_uri as deprecated.
    Strips all existing triples, then writes a minimal stub:
      rdf:type, rdfs:label 'obsolete ...', owl:deprecated true,
      IAO:0100001 (term_replaced_by), efo:obsoleted_in_version,
      rdfs:comment, dc:contributor."""
    # Capture essentials before removing everything
    old_labels = list(g.objects(old_uri, RDFS.label))
    old_types  = list(g.objects(old_uri, RDF.type))

    # Remove all triples for this subject (blank node subgraphs become orphaned
    # and will be swept out when deprecated CBDs are removed from the main graph)
    for p, o in list(g.predicate_objects(old_uri)):
        g.remove((old_uri, p, o))

    # rdf:type (needed for OWL validity)
    for t in old_types:
        g.add((old_uri, RDF.type, t))

    # rdfs:label  "obsolete ..."
    for lbl in old_labels:
        new_lbl = str(lbl).strip()
        if not new_lbl.lower().startswith("obsolete"):
            new_lbl = "obsolete " + new_lbl
        g.add((old_uri, RDFS.label, Literal(new_lbl)))
    if not old_labels:
        short = str(old_uri).split("/")[-1]
        g.add((old_uri, RDFS.label, Literal(f"obsolete {short}")))

    # owl:deprecated true
    g.add((old_uri, OWL_DEP, Literal("true", datatype=XSD.boolean)))

    # IAO:0100001 term_replaced_by
    if new_uri:
        g.add((old_uri, TERM_REPLACED_BY, new_uri))

    # efo:obsoleted_in_version
    g.add((old_uri, OBS_IN_VERSION, Literal(NEW_OBS_VERSION)))

    # rdfs:comment
    if comment:
        g.add((old_uri, RDFS.comment, Literal(comment)))

    # dc:contributor
    g.add((old_uri, DC_CONTRIBUTOR, Literal(NEW_OBS_CONTRIB)))


def cbd_triples(g, uri):
    """Concise Bounded Description: all triples where uri is subject,
    plus transitively all blank-node subgraphs."""
    triples = set()
    visited = set()

    def collect(node):
        if node in visited:
            return
        visited.add(node)
        for p, o in g.predicate_objects(node):
            triples.add((node, p, o))
            if isinstance(o, BNode):
                collect(o)

    collect(uri)
    return triples


# Only these predicates are kept for deprecated term stubs
_STUB_PROPS = {
    RDF.type,
    RDFS.label,
    OWL_DEP,
    TERM_REPLACED_BY,
    OBS_IN_VERSION,     # efo:obsoleted_in_version
    RDFS.comment,
    DC_CONTRIBUTOR,     # dc:contributor
}

def stub_triples(g, uri):
    """Minimal deprecated stub: rdf:type, rdfs:label, owl:deprecated, IAO:0100001."""
    return {(uri, p, o) for p, o in g.predicate_objects(uri) if p in _STUB_PROPS}


def _swo_to_obo(uri):
    """Return the OBO URIRef for any EBI SWO IRI, or None if no remapping needed.

    Cases (checked in order):
    1. Explicit direct mapping (_SWO_DIRECT_MAP): e.g. swo.owl → obo/swo.owl
    2. SWO_XXXXXXX terms (main or any sub-namespace):
         http://www.ebi.ac.uk/swo/[subns/]SWO_{num}  →  http://purl.obolibrary.org/obo/SWO_{num}
    3. Other EBI SWO IRIs not in _SWO_SKIP:
         http://www.ebi.ac.uk/swo/{suffix}  →  http://purl.obolibrary.org/obo/swo/{suffix}
         e.g. has_license, is_license_for, version/6.0
    """
    s = str(uri)
    # Case 1: explicit special mapping
    if s in _SWO_DIRECT_MAP:
        return URIRef(_SWO_DIRECT_MAP[s])
    # Case 2: SWO_XXXXXXX term in any sub-namespace
    m = _SWO_TERM_RE.match(s)
    if m:
        return URIRef(OBO_BASE + m.group(1))
    # Case 3: other EBI SWO IRI (not in skip list)
    if s.startswith(EBI_SWO_BASE) and s not in _SWO_SKIP:
        suffix = s[len(EBI_SWO_BASE):]
        return URIRef(OBO_SWO_BASE + suffix)
    return None


def migrate_swo_namespace(g, deprecated_uris):
    """Rename every EBI SWO IRI to its OBO equivalent in ALL triple positions.

    Two remapping rules (via _swo_to_obo):
      - SWO_XXXXXXX terms (main or any sub-namespace) → obo/SWO_XXXXXXX
      - Other EBI SWO IRIs (has_license, version/6.0, …) → obo/swo/<suffix>

    Subject/predicate rule: deprecated URIs are NOT renamed.
    Object rule: all matching URIs are always renamed.
    """
    dep_set = set(deprecated_uris)

    def _remap_subj(uri):
        if not isinstance(uri, URIRef):
            return uri
        new = _swo_to_obo(uri)
        if new and uri not in dep_set:
            return new
        return uri

    def _remap_obj(uri):
        if not isinstance(uri, URIRef):
            return uri
        new = _swo_to_obo(uri)
        return new if new else uri

    to_remove, to_add = [], []
    for s, p, o in list(g.triples((None, None, None))):
        new_s = _remap_subj(s)
        new_p = _remap_subj(p)   # properties used as predicates follow subject rule
        new_o = _remap_obj(o)
        if new_s != s or new_p != p or new_o != o:
            to_remove.append((s, p, o))
            to_add.append((new_s, new_p, new_o))

    for t in to_remove:
        g.remove(t)
    for t in to_add:
        g.add(t)

    return len(to_remove)


def is_deprecated(g, uri):
    for v in g.objects(uri, OWL_DEP):
        if str(v).lower() == "true":
            return True
    return False


def get_label(g, uri):
    lbl = g.value(uri, RDFS.label)
    return str(lbl).strip() if lbl else str(uri).split("/")[-1]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = datetime.date.today().isoformat()

    # --- Backup ---
    backup = SWO_OWL + f".bak_{today}"
    if not os.path.exists(backup):
        shutil.copy2(SWO_OWL, backup)
        print(f"Backup: {backup}")

    sw_backup = SW_OWL + f".bak_{today}"
    if not os.path.exists(sw_backup):
        shutil.copy2(SW_OWL, sw_backup)
        print(f"Backup: {sw_backup}")

    # --- Load ---
    print("Parsing swo-edit.owl …")
    g = Graph()
    g.parse(SWO_OWL)
    print(f"  {len(g)} triples loaded")

    # --- Phase 1: Mint new terms ---
    print("\nPhase 1: Minting new terms …")
    for num, p_iri, c_iri, new1_iri, new1_note, new2_iri in ASSIGNMENTS:
        p_uri  = URIRef(p_iri)
        c_uri  = URIRef(c_iri)
        new1_u = URIRef(new1_iri)
        new2_u = URIRef(new2_iri)

        # New term for col2 (only if it needs replacing)
        if new1_note != "retained":
            p_lbl = get_label(g, p_uri)
            copy_term(g, p_uri, new1_u)
            # Remove the owl:deprecated marker that was copied (new term is not deprecated)
            g.remove((new1_u, OWL_DEP, None))
            g.remove((new1_u, TERM_REPLACED_BY, None))
            print(f"  Minted {new1_iri}  ← copy of {p_iri}  ({p_lbl})")

        # New term for col3 (always minted)
        c_lbl = get_label(g, c_uri)
        copy_term(g, c_uri, new2_u)
        g.remove((new2_u, OWL_DEP, None))
        g.remove((new2_u, TERM_REPLACED_BY, None))
        print(f"  Minted {new2_iri}  ← copy of {c_iri}  ({c_lbl})")

    # --- Phase 2: Update all object references ---
    print("\nPhase 2: Updating axiom references …")
    for num, p_iri, c_iri, new1_iri, new1_note, new2_iri in ASSIGNMENTS:
        new2_u = URIRef(new2_iri)
        c_uri  = URIRef(c_iri)
        # Update col3 references (c_iri → new2)
        n = sum(1 for _ in g.triples((None, None, c_uri)))
        if n > 0:
            update_object_references(g, c_uri, new2_u)
            print(f"  {c_iri} → {new2_iri}  ({n} refs)")

        if new1_note != "retained":
            p_uri  = URIRef(p_iri)
            new1_u = URIRef(new1_iri)
            n = sum(1 for _ in g.triples((None, None, p_uri)))
            if n > 0:
                update_object_references(g, p_uri, new1_u)
                print(f"  {p_iri} → {new1_iri}  ({n} refs)")

    def _obs_comment(num, new_iri_str):
        """Build the standard deprecation comment, using the final OBO IRI."""
        new_uri = URIRef(new_iri_str)
        obo_iri = _swo_to_obo(new_uri) or new_uri
        return (
            f"Deprecated due to consolidation of SWO: and SWO[X]: prefix namespace "
            f"where numeric SWO_{num} collisions occurred. "
            f"Replaced by {obo_iri}."
        )

    # --- Phase 3: Deprecate old terms ---
    print("\nPhase 3: Adding deprecation annotations …")
    for num, p_iri, c_iri, new1_iri, new1_note, new2_iri in ASSIGNMENTS:
        c_uri  = URIRef(c_iri)
        new2_u = URIRef(new2_iri)
        deprecate_term(g, c_uri, new2_u, _obs_comment(num, new2_iri))
        print(f"  Deprecated: {c_iri}  → {new2_iri}")

        if new1_note != "retained":
            p_uri  = URIRef(p_iri)
            new1_u = URIRef(new1_iri)
            deprecate_term(g, p_uri, new1_u, _obs_comment(num, new1_iri))
            print(f"  Deprecated: {p_iri}  → {new1_iri}")

    # --- Phase 4: Collect all deprecated IRIs ---
    print("\nPhase 4: Collecting all deprecated terms …")
    deprecated_uris = set()
    for s in g.subjects(OWL_DEP, None):
        if isinstance(s, URIRef) and is_deprecated(g, s):
            deprecated_uris.add(s)
    print(f"  {len(deprecated_uris)} deprecated terms total")

    # --- Phase 4.5: Migrate SWO_ namespace → OBO ---
    print(f"\nPhase 4.5: Migrating {SWO_BASE}  →  {OBO_BASE} …")
    n_migrated = migrate_swo_namespace(g, deprecated_uris)
    print(f"  {n_migrated} triples updated")

    # --- Phase 4.6: Migrate swo-software.owl namespace ---
    print(f"\nPhase 4.6: Migrating {SW_OWL} …")
    g_sw = Graph()
    g_sw.parse(SW_OWL, format="xml")
    print(f"  {len(g_sw)} triples loaded")
    # No deprecated terms live in swo-software.owl — migrate everything
    n_sw = migrate_swo_namespace(g_sw, set())
    print(f"  {n_sw} triples updated")
    g_sw.serialize(SW_OWL, format="xml")
    print(f"  Wrote {SW_OWL}")

    # --- Phase 5: Build deprecated.owl graph (minimal stubs only) ---
    print("\nPhase 5: Building imports/deprecated.owl …")
    dep_g = Graph()
    dep_ont = URIRef(DEP_ONT_IRI)
    dep_g.add((dep_ont, RDF.type, OWL.Ontology))
    dep_g.add((dep_ont, RDFS.label, Literal("SWO Deprecated Terms")))
    dep_g.add((dep_ont, RDFS.comment,
               Literal("Deprecated terms extracted from swo-edit.owl. "
                       "Imported automatically by the main ontology.")))

    for dep_uri in deprecated_uris:
        for t in stub_triples(g, dep_uri):
            dep_g.add(t)
    print(f"  {len(dep_g)} triples in deprecated.owl")

    # --- Phase 6: Remove full CBD of deprecated terms from main graph ---
    print("\nPhase 6: Removing deprecated terms from main graph …")
    all_dep_triples = set()
    for dep_uri in deprecated_uris:
        all_dep_triples.update(cbd_triples(g, dep_uri))
    for t in all_dep_triples:
        g.remove(t)
    print(f"  Main graph now has {len(g)} triples")

    # --- Phase 7: Add owl:imports for deprecated.owl ---
    ont_iri = g.value(None, RDF.type, OWL.Ontology)
    if ont_iri:
        g.add((ont_iri, OWL.imports, URIRef(DEP_ONT_IRI)))
        print(f"\nPhase 7: Added owl:imports <{DEP_ONT_IRI}> to <{ont_iri}>")

    # --- Phase 8: Serialize ---
    print("\nPhase 8: Writing files …")
    g.serialize(SWO_OWL, format="xml")
    print(f"  Wrote {SWO_OWL}")

    os.makedirs(os.path.dirname(DEP_OWL), exist_ok=True)
    dep_g.serialize(DEP_OWL, format="xml")
    print(f"  Wrote {DEP_OWL}")

    # --- Phase 9: Update catalog-v001.xml ---
    print("\nPhase 9: Updating catalog-v001.xml …")
    with open(CATALOG, "r", encoding="utf-8") as f:
        cat = f.read()

    entry = f'  <uri name="{DEP_ONT_IRI}" uri="imports/deprecated.owl"/>'
    if entry not in cat:
        cat = cat.replace("</group>", f"{entry}\n\n\n</group>")
        with open(CATALOG, "w", encoding="utf-8") as f:
            f.write(cat)
        print(f"  Added catalog entry for deprecated.owl")
    else:
        print(f"  Catalog entry already present")

    print("\nDone.")


if __name__ == "__main__":
    main()
