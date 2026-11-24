#!/usr/bin/env python3
"""
software.py - Extract software subclass annotations/properties to TSV, build
              swo-software.owl via robot template, and/or remove those axioms
              from swo-edit.owl via robot unmerge.

Usage:
  python software.py                # only write software.tsv (default)
  python software.py --build        # extract TSV then build components/swo-software.owl
  python software.py --build-only   # skip TSV extraction; only run robot template
  python software.py --unmerge      # extract TSV, build OWL, then run robot unmerge
  python software.py --unmerge-only # skip extraction/build; only run robot unmerge

Workflow for one-time migration:
  1. python software.py --build     # extract TSV + build swo-software.owl
  2. python software.py --unmerge-only # remove those axioms from swo-edit.owl
"""
import argparse
import csv
import os
import subprocess
import sys

from rdflib import Graph, URIRef, Literal, BNode, Namespace
from rdflib.namespace import OWL, RDF, RDFS

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
SRC_DIR      = os.path.dirname(os.path.dirname(SCRIPT_DIR))   # src/templates/editor → src/
OWL_PATH     = os.path.join(SRC_DIR, "ontology", "swo-edit.owl")
TSV_PATH     = os.path.join(SRC_DIR, "templates", "software.tsv")

# ---------------------------------------------------------------------------
# Key IRIs (OBO namespace)
# ---------------------------------------------------------------------------
OBO = "http://purl.obolibrary.org/obo/"

SOFTWARE_CLASS       = URIRef(OBO + "SWO_0000001")  # software
HAS_HOMEPAGE         = URIRef(OBO + "SWO_0004006")  # has website homepage
HAS_LICENSE          = URIRef(OBO + "swo/has_license")  # has license
HAS_INTERFACE        = URIRef(OBO + "SWO_0004001")  # has interface
IS_EXECUTED_IN       = URIRef(OBO + "SWO_0040005")  # is executed in
HAS_DATA_INPUT       = URIRef(OBO + "SWO_0000086")  # has specified data input
HAS_DATA_OUTPUT      = URIRef(OBO + "SWO_0000087")  # has specified data output
HAS_FORMAT_SPEC      = URIRef(OBO + "SWO_0004002")  # has format specification
IS_ENCODED_IN        = URIRef(OBO + "SWO_0000741")  # is encoded in
IS_PUBLISHED_BY      = URIRef(OBO + "SWO_0004004")  # is published by
IMPLEMENTS           = URIRef(OBO + "SWO_0000740")  # implements
USES_PLATFORM        = URIRef(OBO + "SWO_0000150")  # uses platform
HAS_VERSION          = URIRef(OBO + "SWO_0004000")  # has version
IS_DEVELOPED_BY      = URIRef(OBO + "SWO_0000394")  # is developed by
HAS_DOCUMENTATION    = URIRef(OBO + "SWO_0000043")  # has documentation
USES_SOFTWARE        = URIRef(OBO + "SWO_0000082")  # uses software
HAS_DECLARED_STATUS  = URIRef(OBO + "SWO_9000067")  # has declared status
HAS_RELEASE_DATE     = URIRef(OBO + "SWO_9000068")  # has release date
HAS_DOWNLOAD_LOC     = URIRef(OBO + "SWO_0000046")  # has download location
IS_SOFTWARE_FOR      = URIRef(OBO + "SWO_0000157")  # is software for
DIRECTLY_FOLLOWED_BY = URIRef(OBO + "SWO_0000132")  # directly followed by
DIRECTLY_PRECEDED_BY = URIRef(OBO + "SWO_0000131")  # directly preceded by
HAS_SUPPORT_URL      = URIRef(OBO + "SWO_0000784")  # has support URL
HAS_PART             = URIRef("http://www.obofoundry.org/ro/ro.owl#has_part")

SW_DEVELOPMENT  = URIRef(OBO + "SWO_9000056")  # software development process
SW_PUBLISHING   = URIRef(OBO + "SWO_9000055")  # software publishing process

OUTPUT_OF       = URIRef(OBO + "RO_0002353")
HAS_PARTICIPANT = URIRef(OBO + "RO_0000057")
IAO_DEFINITION  = URIRef(OBO + "IAO_0000115")
IAO_DEF_SOURCE  = URIRef(OBO + "IAO_0000119")
DCTERMS_CREATOR = URIRef("http://purl.org/dc/terms/creator")

# ---------------------------------------------------------------------------
# CURIE map — prefix → namespace, used by iri_to_curie()
# ---------------------------------------------------------------------------
CURIE_MAP = [
    ("SWO:",  "http://purl.obolibrary.org/obo/SWO_"),
    ("RO:",   "http://purl.obolibrary.org/obo/RO_"),
    ("IAO:",  "http://purl.obolibrary.org/obo/IAO_"),
    ("OBI:",  "http://purl.obolibrary.org/obo/OBI_"),
    ("EDAM:", "http://edamontology.org/"),
    ("ODME:", "http://kt.ijs.si/panovp/OntoDM#"),
]


def iri_to_curie(iri_str):
    """Convert a full IRI string to a CURIE using CURIE_MAP."""
    for prefix, namespace in CURIE_MAP:
        if iri_str.startswith(namespace):
            return prefix + iri_str[len(namespace):]
    return "<{}>".format(iri_str)


def iri_to_label(iri_str, labels):
    """Return the rdfs:label for iri_str, falling back to its CURIE form."""
    lbl = labels.get(URIRef(iri_str))
    if lbl:
        return lbl
    return iri_to_curie(iri_str)


# ---------------------------------------------------------------------------
# TSV headers and ROBOT codes — aligned with schema.yaml slot names
# ---------------------------------------------------------------------------
# ROBOT template requires properties to be referenced by their rdfs:label
# (single-quoted) rather than by CURIE.  Using CURIEs causes ROBOT to
# misidentify object properties as data properties and reject class fillers.
# Data input/output filler values are CURIEs (not labels) for the same reason.
HEADERS = [
    "type",
    "ontology_id",
    "label",
    "parent",
    "definition",
    "definition_source",
    "comment",
    "term_creator",
    "has_specified_data_input",
    "has_specified_data_input_datasets",
    "has_specified_data_output",
    "has_specified_data_output_datasets",
    "is_encoded_in",
    "is_published_by",
    "implements",
    "output_of",
    "is_executed_in",
    "uses_platform",
    "has_license",
    "has_interface",
    "has_website_homepage",
    "has_version",
    "has_part",
    "is_developed_by",
    "has_documentation",
    "uses_software",
    "has_declared_status",
    "has_release_date",
    "has_download_location",
    "is_software_for",
    "directly_followed_by",
    "directly_preceded_by",
    "has_support_url",
]

ROBOT_CODES = [
    "TYPE",
    "ID",
    "AL rdfs:label@en",
    "SC %",
    "AL IAO:0000115@en",
    "A IAO:0000119 SPLIT=|",
    "AL rdfs:comment@en",
    "A dcterms:creator SPLIT=|",
    "SC 'has specified data input' some % SPLIT=|",
    "SC 'has specified data input' some % SPLIT=|",
    "SC 'has specified data output' some % SPLIT=|",
    "SC 'has specified data output' some % SPLIT=|",
    "SC 'is encoded in' some % SPLIT=|",
    "SC 'is published by' value %",
    "SC 'implements' some % SPLIT=|",
    "SC 'output of' some (SWO:9000055 and 'has participant' value %) SPLIT=|",
    "SC 'is executed in' some % SPLIT=|",
    "SC 'uses platform' some % SPLIT=|",
    "SC 'has license' some % SPLIT=|",
    "SC 'has interface' some % SPLIT=|",
    "SC 'has website homepage' value %",
    "SC 'has version' value %",
    "SC 'has part' some % SPLIT=|",
    "SC 'is developed by' value % SPLIT=|",
    "SC 'has documentation' value %",
    "SC 'uses software' some % SPLIT=|",
    "SC 'has declared status' some % SPLIT=|",
    "SC 'has release date' value %",
    "SC 'has download location' value %",
    "SC 'is software for' some % SPLIT=|",
    "SC 'directly followed by' some % SPLIT=|",
    "SC 'directly preceded by' some % SPLIT=|",
    "SC 'has support URL' value %",
]

# Properties whose someValuesFrom filler label goes directly into one column.
# Maps property URIRef → header name.
_SOME_TO_COL = {
    IS_ENCODED_IN:        "is_encoded_in",
    IMPLEMENTS:           "implements",
    USES_PLATFORM:        "uses_platform",
    HAS_PART:             "has_part",
    USES_SOFTWARE:        "uses_software",
    HAS_DECLARED_STATUS:  "has_declared_status",
    IS_SOFTWARE_FOR:      "is_software_for",
    DIRECTLY_FOLLOWED_BY: "directly_followed_by",
    DIRECTLY_PRECEDED_BY: "directly_preceded_by",
    IS_EXECUTED_IN:       "is_executed_in",
    HAS_INTERFACE:        "has_interface",
    HAS_LICENSE:          "has_license",
}

# Properties whose hasValue filler is a Literal; value is quoted for ROBOT.
_VALUE_LIT_TO_COL = {
    HAS_DOCUMENTATION: "has_documentation",
    HAS_RELEASE_DATE:  "has_release_date",
    HAS_DOWNLOAD_LOC:  "has_download_location",
    HAS_SUPPORT_URL:   "has_support_url",
    HAS_HOMEPAGE:      "has_website_homepage",
}

# Properties whose hasValue filler is a URIRef; label is used (single-value).
_VALUE_URI_TO_COL = {
    IS_PUBLISHED_BY: "is_published_by",
    HAS_VERSION:     "has_version",
}

# Properties whose hasValue filler is a URIRef; label is used (multi-value via |).
_VALUE_URI_MULTI_TO_COL = {
    IS_DEVELOPED_BY: "is_developed_by",
}

# ---------------------------------------------------------------------------
# RDF helpers
# ---------------------------------------------------------------------------

def build_label_map(g):
    """Return {URIRef: label_str} for all rdfs:label triples."""
    labels = {}
    for s, _, o in g.triples((None, RDFS.label, None)):
        labels[s] = str(o)
    return labels


def rdf_list_items(g, list_node):
    """Iterate items in an rdf:List."""
    current = list_node
    while current and current != RDF.nil:
        first = g.value(current, RDF.first)
        if first is not None:
            yield first
        current = g.value(current, RDF.rest)


def parse_restriction(g, bnode):
    """
    Given a blank node that is an owl:Restriction, return
    (prop, kind, filler) where kind is 'someValuesFrom' or 'hasValue'.
    Returns None if not a recognized restriction.
    """
    if g.value(bnode, RDF.type) != OWL.Restriction:
        return None
    prop  = g.value(bnode, OWL.onProperty)
    some  = g.value(bnode, OWL.someValuesFrom)
    has   = g.value(bnode, OWL.hasValue)
    all_  = g.value(bnode, OWL.allValuesFrom)
    if some is not None:
        return (prop, "someValuesFrom", some)
    if has is not None:
        return (prop, "hasValue", has)
    if all_ is not None:
        return (prop, "allValuesFrom", all_)
    return None


def intersection_parts(g, bnode):
    """Return items in an owl:intersectionOf list."""
    list_node = g.value(bnode, OWL.intersectionOf)
    if list_node is None:
        return []
    return list(rdf_list_items(g, list_node))


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extract_format(g, filler):
    """
    From a someValuesFrom filler that is an intersectionOf, extract the
    format CURIE from a HAS_FORMAT_SPEC someValuesFrom restriction inside it.
    Returns the CURIE string (e.g. 'SWO:3000004'), or None if pattern doesn't
    match or the IRI falls outside all known prefixes.
    """
    if not isinstance(filler, BNode):
        return None
    for item in intersection_parts(g, filler):
        if isinstance(item, BNode):
            r = parse_restriction(g, item)
            if r and r[0] == HAS_FORMAT_SPEC and r[1] == "someValuesFrom":
                fmt = r[2]
                if isinstance(fmt, URIRef):
                    curie = iri_to_curie(str(fmt))
                    if not curie.startswith("<"):
                        return curie
    return None


def extract_row(g, cls_iri, labels):
    """Extract all template column values for a software subclass IRI."""
    row = {h: "" for h in HEADERS}
    row["type"] = "class"

    # Ontology ID: shorten to SWO:XXXXXXX CURIE
    iri_str = str(cls_iri)
    swo_prefix = OBO + "SWO_"
    if iri_str.startswith(swo_prefix):
        row["ontology_id"] = "SWO:" + iri_str[len(swo_prefix):]
    else:
        row["ontology_id"] = iri_str

    # rdfs:label
    lbl = g.value(cls_iri, RDFS.label)
    if lbl:
        row["label"] = str(lbl)

    # parent: most specific named superclass
    named_parents = [sc for sc in g.objects(cls_iri, RDFS.subClassOf)
                     if isinstance(sc, URIRef)]
    if named_parents:
        parent_iri = next(
            (p for p in named_parents if p != SOFTWARE_CLASS),
            named_parents[0],
        )
        row["parent"] = iri_to_label(str(parent_iri), labels)
    else:
        row["parent"] = iri_to_label(str(SOFTWARE_CLASS), labels)

    # Annotation properties
    defn = g.value(cls_iri, IAO_DEFINITION)
    if defn:
        row["definition"] = str(defn)

    def_sources = [str(o) for o in g.objects(cls_iri, IAO_DEF_SOURCE)]
    row["definition_source"] = "|".join(def_sources)

    comment = g.value(cls_iri, RDFS.comment)
    if comment:
        row["comment"] = str(comment)

    creators = [str(o) for o in g.objects(cls_iri, DCTERMS_CREATOR)]
    row["term_creator"] = "|".join(creators)

    # Collect multi-valued columns
    multi = {h: [] for h in HEADERS if h not in row or row[h] == ""}

    for sc_obj in g.objects(cls_iri, RDFS.subClassOf):
        if isinstance(sc_obj, URIRef):
            continue
        if not isinstance(sc_obj, BNode):
            continue

        restriction = parse_restriction(g, sc_obj)
        if restriction:
            _handle_restriction(g, restriction, labels, row, multi)
        else:
            # top-level intersectionOf — recurse into members
            for item in intersection_parts(g, sc_obj):
                if isinstance(item, BNode):
                    r = parse_restriction(g, item)
                    if r:
                        _handle_restriction(g, r, labels, row, multi)

    # Flatten multi-valued columns
    for col, vals in multi.items():
        if vals:
            row[col] = "|".join(vals)

    return [row[h] for h in HEADERS]


def _handle_restriction(g, restriction, labels, row, multi):
    """Dispatch a parsed restriction to the appropriate output column(s)."""
    prop, kind, filler = restriction

    # --- simple someValuesFrom → CURIE (labels break Manchester if they contain
    #     apostrophes or spaces; CURIEs are always safe) ---
    if kind == "someValuesFrom" and prop in _SOME_TO_COL:
        col = _SOME_TO_COL[prop]
        if isinstance(filler, URIRef):
            curie = iri_to_curie(str(filler))
            if not curie.startswith("<"):
                multi[col].append(curie)
        elif isinstance(filler, BNode):
            # union or simple intersection — flatten named classes
            for item in intersection_parts(g, filler):
                if isinstance(item, URIRef):
                    curie = iri_to_curie(str(item))
                    if not curie.startswith("<"):
                        multi[col].append(curie)
        return

    # --- hasValue with Literal → quoted string ---
    if kind == "hasValue" and prop in _VALUE_LIT_TO_COL:
        col = _VALUE_LIT_TO_COL[prop]
        row[col] = '"{}"'.format(str(filler))
        return

    # --- hasValue with URIRef → label (single-value) ---
    if kind == "hasValue" and prop in _VALUE_URI_TO_COL:
        col = _VALUE_URI_TO_COL[prop]
        if isinstance(filler, URIRef):
            row[col] = iri_to_label(str(filler), labels)
        return

    # --- hasValue with URIRef → label (multi-value) ---
    if kind == "hasValue" and prop in _VALUE_URI_MULTI_TO_COL:
        col = _VALUE_URI_MULTI_TO_COL[prop]
        if isinstance(filler, URIRef):
            multi[col].append(iri_to_label(str(filler), labels))
        return

    # --- has_specified_data_input ---
    if prop == HAS_DATA_INPUT and kind == "someValuesFrom":
        if isinstance(filler, URIRef):
            # simple named class → datasets column (CURIE for ROBOT)
            curie = iri_to_curie(str(filler))
            if not curie.startswith("<"):
                multi["has_specified_data_input_datasets"].append(curie)
        elif isinstance(filler, BNode):
            fmt = _extract_format(g, filler)
            if fmt:
                # intersection with format spec → format column (CURIE)
                multi["has_specified_data_input"].append(fmt)
            else:
                # intersection of named classes (no format) → datasets column (CURIE)
                for item in intersection_parts(g, filler):
                    if isinstance(item, URIRef):
                        curie = iri_to_curie(str(item))
                        if not curie.startswith("<"):
                            multi["has_specified_data_input_datasets"].append(curie)
        return

    # --- has_specified_data_output ---
    if prop == HAS_DATA_OUTPUT and kind == "someValuesFrom":
        if isinstance(filler, URIRef):
            # direct named class → datasets column (CURIE for ROBOT)
            curie = iri_to_curie(str(filler))
            if not curie.startswith("<"):
                multi["has_specified_data_output_datasets"].append(curie)
        elif isinstance(filler, BNode):
            fmt = _extract_format(g, filler)
            if fmt:
                # intersection with format spec → format column (CURIE)
                multi["has_specified_data_output"].append(fmt)
            else:
                # intersection of named classes (no format) → datasets column (CURIE)
                for item in intersection_parts(g, filler):
                    if isinstance(item, URIRef):
                        curie = iri_to_curie(str(item))
                        if not curie.startswith("<"):
                            multi["has_specified_data_output_datasets"].append(curie)
        return

    # --- output_of (developer or publisher via process) ---
    if prop == OUTPUT_OF and kind == "someValuesFrom":
        process_cls = None
        org_val = None
        for item in intersection_parts(g, filler):
            if isinstance(item, URIRef):
                process_cls = item
            elif isinstance(item, BNode):
                r = parse_restriction(g, item)
                if r and r[0] == HAS_PARTICIPANT and r[1] == "hasValue":
                    org_val = r[2]
        if org_val is None:
            return
        if process_cls == SW_PUBLISHING:
            # Publisher org → output_of column
            multi["output_of"].append(iri_to_label(str(org_val), labels))
        elif process_cls == SW_DEVELOPMENT:
            # Developer org → normalize to is_developed_by
            multi["is_developed_by"].append(iri_to_label(str(org_val), labels))


def extract_to_tsv(g, software_classes, labels, tsv_path):
    """Write software.tsv from all software subclasses."""
    rows = []
    for cls_iri in sorted(software_classes, key=str):
        row = extract_row(g, cls_iri, labels)
        rows.append(row)

    with open(tsv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(HEADERS)
        writer.writerow(ROBOT_CODES)
        for row in rows:
            clean = [
                c.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
                if isinstance(c, str) else c
                for c in row
            ]
            writer.writerow(clean)

    print("Wrote {} rows to {}".format(len(rows), tsv_path))


# ---------------------------------------------------------------------------
# Build via robot template, and removal via robot unmerge
# ---------------------------------------------------------------------------

ONTOLOGY_DIR   = os.path.join(SRC_DIR, "ontology")
COMPONENT_PATH = os.path.join(ONTOLOGY_DIR, "components", "swo-software.owl")
COMPONENT_IRI  = "http://purl.obolibrary.org/obo/swo/components/swo-software.owl"
ROBOT_CMD      = os.environ.get("ROBOT", "robot")


def robot_build():
    """
    Build components/swo-software.owl from software.tsv using robot merge chained
    into robot template.  The merge step resolves the full import closure of
    swo-edit.owl so that all labels and entity types are available to the template
    Manchester-syntax parser.

    Equivalent shell command (run from src/ontology/):
      robot merge  --input swo-edit.owl \\
            template \\
              --prefix "obo: http://purl.obolibrary.org/obo/" \\
              --xml-entities \\
              --ontology-iri "<COMPONENT_IRI>" \\
              --template ../templates/software.tsv \\
            annotate \\
              --ontology-iri "<COMPONENT_IRI>" \\
              --output components/swo-software.owl
    """
    if not os.path.exists(COMPONENT_PATH):
        print("ERROR: {} does not exist.".format(COMPONENT_PATH), file=sys.stderr)
        sys.exit(1)

    cmd = [
        ROBOT_CMD, "merge",
        "--input", "swo-edit.owl",
        "template",
        "--prefix", "obo: http://purl.obolibrary.org/obo/",
        "--prefix", "RO: http://purl.obolibrary.org/obo/RO_",
        "--prefix", "IAO: http://purl.obolibrary.org/obo/IAO_",
        "--prefix", "SWO: http://purl.obolibrary.org/obo/SWO_",
        "--prefix", "EDAM: http://edamontology.org/",
        "--prefix", "ODME: http://kt.ijs.si/panovp/OntoDM#",
        "--prefix", "dcterms: http://purl.org/dc/terms/",
        "--xml-entities",
        "--ontology-iri", COMPONENT_IRI,
        "--template", TSV_PATH,
        "annotate",
        "--ontology-iri", COMPONENT_IRI,
        "--output", COMPONENT_PATH,
    ]
    print("Running: " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ONTOLOGY_DIR)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        print("ERROR: robot build failed (exit {})".format(result.returncode),
              file=sys.stderr)
        sys.exit(result.returncode)
    print("Built {}".format(COMPONENT_PATH))


def robot_unmerge():
    """
    Run robot unmerge to remove from swo-edit.owl all axioms present in
    swo-software.owl (the ROBOT template output).
    """
    if not os.path.exists(COMPONENT_PATH):
        print("ERROR: swo-software.owl not found at {}".format(COMPONENT_PATH),
              file=sys.stderr)
        sys.exit(1)

    cmd = [
        ROBOT_CMD, "unmerge",
        "--input",  OWL_PATH,
        "--input",  COMPONENT_PATH,
        "--output", OWL_PATH,
    ]
    print("Running: " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        print("ERROR: robot unmerge failed (exit {})".format(result.returncode),
              file=sys.stderr)
        sys.exit(result.returncode)
    print("robot unmerge complete: axioms in swo-software.owl removed from swo-edit.owl")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--build", action="store_true",
                       help="Extract TSV then build components/swo-software.owl via robot template")
    group.add_argument("--build-only", action="store_true",
                       help="Skip TSV extraction; only run robot template to build swo-software.owl")
    group.add_argument("--unmerge", action="store_true",
                       help="Extract TSV, build swo-software.owl, then run robot unmerge")
    group.add_argument("--unmerge-only", action="store_true",
                       help="Skip extraction/build; only run robot unmerge "
                            "(requires swo-software.owl to already exist)")
    args = parser.parse_args()

    do_extract = not (args.build_only or args.unmerge_only)
    do_build   = args.build or args.build_only or args.unmerge
    do_unmerge = args.unmerge or args.unmerge_only

    if do_extract:
        print("Loading {} ...".format(OWL_PATH))
        g = Graph()
        g.parse(OWL_PATH, format="xml")
        print("  {} triples loaded".format(len(g)))

        labels = build_label_map(g)
        print("  {} labels indexed".format(len(labels)))

        software_classes = set()
        for s in g.subjects(RDFS.subClassOf, SOFTWARE_CLASS):
            if isinstance(s, URIRef):
                software_classes.add(s)
        print("  Found {} direct subclasses of 'software'".format(len(software_classes)))

        extract_to_tsv(g, software_classes, labels, TSV_PATH)

    if do_build:
        robot_build()

    if do_unmerge:
        robot_unmerge()


if __name__ == "__main__":
    main()
