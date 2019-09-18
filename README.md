--------------------------------
# The Software Ontology
The Software Ontology (SWO) is a resource for describing software tools, their types, tasks, versions, provenance and associated data. A new release (1.7) is currently in progress, and can be monitored at https://github.com/allysonlister/swo/milestone/1

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

1. Releases occur on an *ad hoc* basis, as required by our users. All releases of the Software Ontology can always be found at https://github.com/allysonlister/swo/tree/master/release.

2. Assigned identifiers will remain on a class and will not be deleted from the OWL files. Classes that are no longer required will be subclassed under the *obsolete SWO class* (child of *DeprecatedClass*) and stripped of axioms but the URI and original annotation will remain.

3. Classes that change their intended meaning significantly (i.e. they denote a new entity) will be assigned a new URI and the old URI will be made obsolete as per the above.

4. A release notes file will be attached to each release as a README.md and will outline major changes to the ontology.

Release files are created using [ROBOT](http://robot.obolibrary.org/) via a controlling [Makefile](development/Makefile).

# Tools Used

[Ontofox](http://ontofox.hegroup.org/) has been used to build [swo-external](https://github.com/allysonlister/swo/blob/master/dev/ontology/swo-external.owl) containing all of the imported classes and associated axioms. [Protege](https://protege.stanford.edu/) (including versions 4.3.0 and 5.2.0) has been used to create the core OWL file and to view and check all development ontology files. [ROBOT](http://robot.obolibrary.org/) has been used to merge and build the release files as well as perform a number of housekeeping tasks.

- [Ontofox](http://ontofox.hegroup.org/) - Xiang Z, Courtot M, Brinkman RR, Ruttenberg A, He Y. OntoFox: web-based support for ontology reuse.
BMC Research Notes. 2010, 3:175. PMID: 20569493
- [Protege](http://protege.stanford.edu/) - Musen, M.A. [The Protégé project: A look back and a look forward.](http://www.ncbi.nlm.nih.gov/pmc/articles/PMC4883684/) AI Matters. Association of Computing Machinery Specific Interest Group in Artificial Intelligence, 1(4), June 2015. DOI: 10.1145/2557001.25757003.
- [ROBOT](http://robot.obolibrary.org/) - R.C. Jackson, J.P. Balhoff, E. Douglass, N.L. Harris, C.J. Mungall, and J.A. Overton. [ROBOT: A tool for automating ontology workflows](https://rdcu.be/bMnHT). BMC Bioinformatics, vol. 20, July 2019.

# Licensing

The SWO License is available at [License.md](LICENSE.md).

For license information for the external ontologies used to create SWO, please see our [Licensing Compliance](https://github.com/allysonlister/swo/blob/master/LicensingCompliance.md) page.


# Restructuring Prior to Release 1.7

Due to improvements in automatically building an ontology via Ontofox and ROBOT, we no longer needed to import all of EDAM. Release 1.7 was the first to employ these semi-automated updates. This allows SWO to be easily aligned with any new releases of its imported ontologies and creates a much simpler build procedure.

Previously, SWO was built with a series of modular OWL files labelled swo_*.owl. While this made things easier in previous years, the number of development files was getting unwieldy. Currently, the only file imported by the development version of SWO is the automatically-generated one produced by Ontofox, which pulls in all of the latest annotation and hierarchies for the imported classes.
