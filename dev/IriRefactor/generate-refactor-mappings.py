# Read in a list of IRIs and generate refactoring mappings
# output as per ROBOT's rename command: http://robot.obolibrary.org/rename
# to a csv file, refactor-efo-swo-mappings.csv
#
# shared-ids.txt has been generated with the following:
# comm -12 efo-swo-1-ids.txt efo-swo-2-ids.txt > shared-ids.txt
#
# unshared-ids.txt has been generated with the following:
# comm -23 efo-swo-1-ids.txt efo-swo-2-ids.txt > unshared-ids.txt
#

infile = open("shared-ids.txt", "r")
infile2 = open("unshared-ids.txt", "r")
outfile = open("refactor-efo-swo-mappings.csv", "w")

outfile.write("Old IRI,New IRI\n")

counter = 1100000

for line in infile:
    #print(line)
    # Generate new ID starting from a safe space of SWO_1100000
    outfile.write("http://www.ebi.ac.uk/efo/swo/" + line.strip() + ",http://www.ebi.ac.uk/swo/SWO_" + str(counter) + "\n")
    counter += 1

for line in infile2:
    outfile.write("http://www.ebi.ac.uk/efo/swo/" + line.strip() + ",http://www.ebi.ac.uk/swo/" + line.strip() + "\n")

infile.close()
infile2.close()
outfile.close()
