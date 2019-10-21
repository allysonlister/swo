# Software Ontology Development Files

Within the `dev/ontology` subdirectory, you will find a number of OWL files.

* swo.owl: Contains the SWO-specific axioms and annotations, and imports swo-external.owl. Manually curated.
* swo-external.owl: Contains the axioms and annotations from external ontologies required by SWO. Generated via configuration file by Ontofox.
* swo-merged.owl: A version of swo.owl that contains swo.owl and swo-external.owl without needing to import any additional files. Generated via Makefile using ROBOT.


swo-legacy/ is a directory that stores the older SWO files as they were prior to the 1.7 release. They are no longer updated.
