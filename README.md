--------------------------------
# The Software Ontology
The Software Ontology (SWO) is a resource for describing software tools, their types, tasks, versions, provenance and associated data.

# Files

The directories within SWO are organized as follows:

>swo/
>   dev/ontology/
>   release/

## Development files

swo_components contains the individual owl modules that make up SWO. the swo_core file imports the others so should be considered the 'main file'. This includes the import of the EDAM ontology into SWO.

## Release files

Within the release/ subdirectory are the various files that are published with each SWO release. These include:

* swo_inferred.owl - a merged version of the Software Ontology which includes inferences over the axioms. That is, all inferences have been saved as asserted axioms in this version. This is the version to use in viewing the ontology in applications.
* swo_merged.owl - a merged version of the Software Ontology in a single file, with no additional inferences. All SWO modules are simply concatenated into one file.

# Release Policy

1. Releases occur on an *ad hoc* basis, as required by our users. The latest releases of the Software Ontology can always be found at https://github.com/allysonlister/swo/tree/master/src/release. Earlier releases are tagged with their version numbers. Details of accessing those releases is below.

2. Assigned identifiers will remain on a class and will not be deleted from the owl files. classes that are no longer required will be subclassed under the obsolete class and stripped of axioms but the uri will remain.

3. Classes that change their intended meaning significantly (i.e. they denote a new entity) will be assigned a new URI and the old URi will be made obsolete as per the above.

4. A release notes file will be attached to each release. this will appear in the folder trunk\src\release\releasenotes and will outline major changes 

# Downloading Files

## Downloading the latest development version of SWO

## Downloading the latest release of SWO

## Downloading a particular version of SWO

In October 2016, the working version of SWO was moved from Sourceforge to GitHub. From this point on, each release was tagged. This section will show you how to retrieve releases from 1.5 onwards.

1. A list of tagged version can be found with the command

> git 
