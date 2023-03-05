# Read in a list of IRIs and generate refactoring mappings
# output as per ROBOT's rename command: http://robot.obolibrary.org/rename
# to a csv file, refactor-efo-swo-mappings.csv
#
# The resulting csv can be used as follows:
# java -jar ../build/robot.jar rename --input ../ontology/swo-merged.owl --mappings refactor-efo-swo-mappings.csv --output swo-refactored.owl
#
# Although ultimately, when happy with the result, we need to change swo.owl rather than swo-merged.owl.
#
# Then tested by performing a diff, e.g.
# java -jar robot.jar diff --left swo-edit.owl --right swo-refactored.owl --format html --output diff.html
#
# already-existing-iris.txt has been generated with the following:
# comm -23 swo-matches.txt swo-matches-su.txt > already-existing-iris.txt
#
# unshared-ids.txt has been generated with the following:
# comm -13 swo-matches.txt swo-matches-su.txt > unshared-iris.txt
#

infile = open("already-existing-iris.txt", "r")
infile2 = open("unshared-iris.txt", "r")
outfile = open("refactor-efo-swo-mappings.csv", "w")

outfile.write("Old IRI,New IRI\n")

counter = 1200000

for line in infile:
    #print(line)
    # Generate new ID starting from a safe position (counter)
    outfile.write("http://www.ebi.ac.uk/efo/swo/" + line.strip() + ",http://www.ebi.ac.uk/swo/SWO_" + str(counter) + "\n")
    counter += 1

for line in infile2:
    outfile.write("http://www.ebi.ac.uk/efo/swo/" + line.strip() + ",http://www.ebi.ac.uk/swo/" + line.strip() + "\n")

infile.close()
infile2.close()
outfile.close()
