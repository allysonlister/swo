# The Software Ontology Release Notes

https://github.com/allysonlister/swo

* VERSION: 1.7
* DATE: 21 October 2019
* PURL: http://purl.obolibrary.org/obo/swo.owl

The following OWL files are available in this directory:

* swo.owl: The standalone ontology file with the asserted hierarchy and axioms only (inferences not included).
* swo-inferred.owl: The inferred hierarchy, plus all annotation and asserted logical axioms. This is the version of the ontology that our purl redirects to.
* swo-inferred-annotations.owl: The inferred hierarchy, plus all annotations but **no** asserted logical axioms. This provides a useful lightweight view that highlights the hierarchy.

# Summary

Due to improvements in automatically building an ontology via Ontofox and ROBOT, we no longer needed to import all of EDAM. Release 1.7 was the first to employ these semi-automated updates. This allows SWO to be easily aligned with any new releases of its imported ontologies and creates a much simpler build procedure.

Previously, SWO was built with a series of modular OWL files labelled swo_*.owl. While this made things easier in previous years, the number of development files was getting unwieldy. Currently, the only file imported by the development version of SWO is the automatically-generated one produced by Ontofox, which pulls in all of the latest annotation and hierarchies for the imported classes.

Finally, the IRIs across all SWO classes were reconciled according to the official SWO IRI naming scheme. As such, many IRIs were deprecated and new IRIs issued. Full details are available at the [IRI Refactoring Issue](https://github.com/allysonlister/swo/issues/10) in the issue tracker.


# Change log

If you wish to perform a full diff between these versions please use [ROBOT's diff command](http://robot.obolibrary.org/diff).


# Obsolete Classes

A number of IRIs were made obsolete during the work in the [IRI Refactoring Issue](https://github.com/allysonlister/swo/issues/10). All are marked as deprecated in the ontology.
