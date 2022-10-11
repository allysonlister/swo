--------------------------------
# The Software Ontology
The Software Ontology (SWO) is a resource for describing software tools, their types, tasks, versions, provenance and associated data. The latest version is 1.6, released 1 February 2017.

# Files

The directories within SWO are organized as follows:

    swo/
        dev/ontology/
        release/

Each directory contains a README describing its contents. Broadly, the `dev` directory contains the working files which are updated in an ongoing manner. The `release` directory is only updated when a release has been built.

## Development files

Within the `dev/ontology` subdirectory, you will find a number of OWL files. These are the modular files used to build SWO, and can all be loaded together in Protege by simply opening `swo_core.owl`, as this file imports all others in the directory.

## Release files

Within the release/ subdirectory are the various files that are published with each SWO release. These include:

* swo_inferred.owl - a merged version of the Software Ontology which includes inferences over the axioms. That is, all inferences have been saved as asserted axioms in this version. This is the version to use in viewing the ontology in applications.
* swo_merged.owl - a merged version of the Software Ontology in a single file, with no additional inferences. All SWO modules are simply concatenated into one file.

In addition to this, each release is tagged with its release number using the `git tag` command and therefore you can access the individual modules as they were during a release. For more information on retrieving particular tagged versions, please see the section below, "Downloading a particular version of SWO".

# Release Policy

1. Releases occur on an *ad hoc* basis, as required by our users. The latest releases of the Software Ontology can always be found at https://github.com/allysonlister/swo/tree/master/src/release. Earlier releases are tagged with their version numbers. Details of accessing those releases is below.

2. Assigned identifiers will remain on a class and will not be deleted from the OWL files. Classes that are no longer required will be subclassed under the *obsolete SWO class* (child of *DeprecatedClass*) and stripped of axioms but the URI will remain.

3. Classes that change their intended meaning significantly (i.e. they denote a new entity) will be assigned a new URI and the old URI will be made obsolete as per the above.

4. A release notes file will be attached to each release as swo/release/README.md and will outline major changes to the ontology.

Release files are created using [ROBOT](http://robot.obolibrary.org/) via a controlling [Makefile](development/Makefile).

# Downloading Files

## Downloading the latest development version of SWO

Simply clone the repository with `git clone` and then navigate to `swo/dev/ontology`. All development files will be in that directory (open `swo_core.owl` in Protege and all other modules will be automatically imported). Keep track of updates by regularly running `git pull`.

## Downloading the latest release of SWO

Simply clone the repository with `git clone` and then navigate to `swo/release`. All release files will be in that directory (open `swo_merged.owl` in Protege for a simple view of all asserted classes and axioms). Keep track of updates by regularly running `git pull`.

## Downloading a particular version of SWO

In October 2016, the working version of SWO was moved from Sourceforge to GitHub (Release 1.6). From this point on, each release was tagged. This section will show you how to retrieve releases from 1.5 onwards.

1. A list of tagged version can be found with the command

> git tag -l

(The rest of this section is based on http://stackoverflow.com/questions/791959/download-a-specific-tag-with-git)

If you wish to checkout a particular release, then you can clone the whole repository

> git clone

Then list tags with `git tag -l` to find the one you want. You may wish to simply checkout that tag:

> git checkout tags/<tag_name>

Alternatively, create a branch:

>git checkout tags/<tag_name> -b <branch_name>

# Restructuring Prior to Release 1.6

We restructured the directories used in SWO just before we released version 1.6. This restructure deliberately corresponded with the move to GitHub, as it was a good opportunity to clean up. You will find that many files have moved, and as such will seem to have a git history that only seems to stretch back to October 2016. In fact, the entire history of all of our files has been preserved from the initial commits in SVN on Sourceforge through to the restructure that happened in October 2016. However, the web interface to GitHub does not show the history from before the `git mv` command was used.

To see the history of any *moved* file in SWO, you simply need to add the `--follow` command to your `git log` as follows in this example for examining the log for `swo_core.owl`:

> git log --follow swo/dev/ontology/swo_core.owl

If a file has been deleted (as was the case with a number of obsolete SWO files in the release subdirectory, you can find the file by viewing all logs of deletions with

> git log --diff-filter=D --summary

You can then show a particular file you are interested in with `git show`.
