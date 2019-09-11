--------------------------------
# The Software Ontology
The Software Ontology (SWO) is a resource for describing software tools, their types, tasks, versions, provenance and associated data. A new release (1.8) is currently in progress, and can be monitored at https://github.com/allysonlister/swo/milestone/1

# Files

The directories within SWO are organized as follows:

    swo/
        dev/ontology/
        release/

Each directory contains a README describing its contents. Broadly, the `dev` directory contains the working files which are updated in an ongoing manner. The `release` directory is only updated when a release has been built.

## Development files

Within the `dev/ontology` subdirectory, you will find a number of OWL files. These are the modular files used to build SWO, and can all be loaded together in Protege by simply opening `swo.owl`, as this file imports all others in the directory.

## Release files

Within the release/ subdirectory are the various files that are published with each SWO release. These include:

*

# Release Policy

1. Releases occur on an *ad hoc* basis, as required by our users. The latest releases of the Software Ontology can always be found at https://github.com/allysonlister/swo/tree/master/src/release. Earlier releases are tagged with their version numbers. Details of accessing those releases is below.

2. Assigned identifiers will remain on a class and will not be deleted from the OWL files. Classes that are no longer required will be subclassed under the *obsolete SWO class* (child of *DeprecatedClass*) and stripped of axioms but the URI will remain.

3. Classes that change their intended meaning significantly (i.e. they denote a new entity) will be assigned a new URI and the old URI will be made obsolete as per the above.

4. A release notes file will be attached to each release as swo/release/README.md and will outline major changes to the ontology.

Release files are created using [ROBOT](http://robot.obolibrary.org/) via a controlling [Makefile](development/Makefile).

# Restructuring Prior to Release 1.8
