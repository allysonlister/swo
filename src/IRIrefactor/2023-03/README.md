##Background

In 2019, we did a large IRI refactoring when some IRIs containing "efo" crept into the ontology, which were unresolvable (see the [original ticket](https://github.com/allysonlister/swo/issues/10)).

It seems we were not complete enough, and so we're running it again ([new ticket](https://github.com/allysonlister/swo/issues/59)). This directory contains all the scripts and files that were used to perform this change.

## Prior to the changes

### obsolete_MathWorks
During the initial creation of the efo IRIs, we noticed that http://www.ebi.ac.uk/efo/swo/SWO_0000291 (obsolete_MathWorks) had been incorrectly obsoleted some years ago (the owl:deprecated flag hadn't been set), causing it to appear as though it was not yet obsolete. This was dealt with manually, and committed separately, within swo-edit before the more automated steps below were "officially" run.

### OBI_0000245_1
Please note I also obsoleted the following individuals which seem to have been added erroneously:
http://www.ebi.ac.uk/efo/swo/OBI_0000245_1

### obsoleted_in_version and reason_for_obsolescence
We noticed that there were also these two EFO properties, and decided to keep one and refactor the other:
1. Kept http://www.ebi.ac.uk/efo/obsoleted_in_version,obsoleted as it is still present within EFO. However, we don't plan to use this in future, as the related issue will provide more context.
2. Refactored http://www.ebi.ac.uk/efo/reason_for_obsolescence,reason for obsolescence --> http://www.w3.org/2000/01/rdf-schema#comment as EFO no longer has this property within its release files. We will do this by adding it to the mapping file (refactor-efo-swo-mappings.csv) manually, and stripping out all of the annotations assigned to it to prevent it being added to rdfs:comment.

## Steps taken

1. Generate list of all SWO IRIs from non-obsolete terms. From `swo/`:
Get all IRIs.
`java -jar robot.jar query --input swo.owl --query src/sparql/terms.sparql src/IRIrefactor/2023-03/terms.csv`

change to the `swo/src/IRIrefactor/2023-03` directory and then continue. Get all IRIs that are from SWO.
`grep SWO\_ terms.csv | sort -u > swo-terms.csv`

Get all IRIs of terms that are NOT obsolete.
`java -jar robot.jar query --input swo.owl --query src/sparql/iri-not-obsolete.sparql terms-not-obsolete.csv`
Get the subset of those that are from SWO.
`grep SWO\_ terms-not-obsolete.csv | sort -u > swo-terms-not-obsolete.csv`
1240 existing SWO terms (that aren't obsolete). Make a list without their prefixes to use later. (actually, in swo-edit now, as per note above, SWO_0000291 and OBI_0000245_1 are now (correctly) obsolete and so this number is two lower in that file.)
`perl -pe 's/.*\/(SWO_.+)/$1/;' swo-terms-not-obsolete.csv | sort > swo-active-terms.txt`

(As a side effect, generate this list of all obsolete terms for reference)
`comm -13 swo-terms-not-obsolete.csv swo-terms.csv > swo-terms-obsolete.csv`
678 existing obsolete terms (680 in swo-edit due to SWO_0000291 and OBI_0000245_1)

2. Retrieve a list of all of the SWO terms with "efo" within them (that aren't already obsolete)
How many of these 1240 non-obsolete terms have efo within them?
`grep -i efo swo-terms-not-obsolete.csv | sort > swo-efo-iris.txt`
48 with efo in the IRI, but one had to be dealt with manually (SWO_0000291, see above), which means there are 47 going forward.
`grep -i efo swo-terms-not-obsolete.csv | awk -F"/" '{print $6;}' | sort -u > swo-efo-terms.txt`

3. check for a pre-existing "http://www.ebi.ac.uk/swo" IRI that matches it. (If present, I'd need to generate a new SWO_ class number. Otherwise, just update the IRI and retain the class number.)
`grep -f swo-efo-terms.txt swo-active-terms.txt > swo-matches.txt`
`sort -su swo-matches.txt > swo-matches-su.txt`
`comm -23 swo-matches.txt swo-matches-su.txt > already-existing-iris.txt`
`comm -23 swo-matches-su.txt already-existing-iris.txt > unshared-iris.txt`
14 IRIs already exist in SWO and will need to have an IRI change as well.

4. Create refactoring file for EFO IRIs.
`python generate-refactor-mappings.py`

5. Move to `swo/src/ontology`. Refactor of all "efo" IRIs as per the mapping file. This step will transfer all of the axioms / annotations etc to their new IRIs.
`java -jar robot.jar rename --input swo-edit.owl --mappings ../IRIrefactor/2023-03/refactor-efo-swo-mappings.csv --output swo-refactored.owl`

6. Run ROBOT diff to check the actual differences between the two files. The diff looks good, with all axioms being transferred as far as I can tell, and no odd differences between the two files.
`java -jar robot.jar diff --left swo-edit.owl --right swo-refactored.owl --format html --output diff-step6.html`

7. Go back to `swo/src/IRIrefactor/2023-03`. Create a list of iris and labels for all of swo, then filter according to those with 'efo' present
`java -jar ~/Programs/robot.jar query --input ../../ontology/swo-edit.owl --query iri-label-not-obsolete.sparql  iri-label.txt`
`grep '\/efo\/' iri-label.txt > efo-iri-label.txt`
51 lines
In the initial run, there were three caught here that weren't caught by earlier checks due to the fact they they didn't match the search strings. See the top of the page for the manual fixes done for those. There were also three with quotes in their names, you'll need to be careful to remove those so the generated sparql rules work.

8. Read in a list of IRIs + labels and generate deprecation annotation for all of them. This will create the SPARQL update code needed to run over the new version of the owl edit file.
`python generate-deprecation-annotation.py`

At this stage the code presumes that all refactored items are instances, when in fact we have two properties, `http://www.ebi.ac.uk/efo/swo/SWO_0000741,is encoded in` and also `http://www.ebi.ac.uk/efo/reason_for_obsolescence`. For this please adjust the annotation in the resulting `.ru` file manually.

9. Move to `swo/src/ontology`. Add back all the original IRIs as obsolete terms with appropriate deprecated annotation.
`java -jar robot.jar query --input swo-refactored.owl --update ../IRIrefactor/2023-03/deprecation-annotation.ru  --output swo-completed.owl`

Test that this looks OK compared to the refactored version from step 6.
`java -jar robot.jar diff --left swo-refactored.owl --right swo-completed.owl --format html --output diff-step9.html`
All looks good.

