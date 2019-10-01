# Read in a list of IRIs and generate deprecation annotation for all of them.
# output as SPARQL update code (deprecation-annotation.ru)

infile = open("efo-swo-1.txt", "r")
outfile = open("deprecation-annotation.ru", "w")

outfile.write("prefix oboInOwl: <http://www.geneontology.org/formats/oboInOwl#>\n")
outfile.write("prefix obo: <http://purl.obolibrary.org/obo/>\n")
outfile.write("prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n")
outfile.write("prefix owl: <http://www.w3.org/2002/07/owl#>\n")
outfile.write("prefix efo: <http://www.ebi.ac.uk/efo/>\n")
outfile.write("prefix efoswo: <http://www.ebi.ac.uk/efo/swo/>\n")
outfile.write("prefix swo: <http://www.ebi.ac.uk/swo/>\n")
outfile.write("\n")
outfile.write("INSERT {\n")

for line in infile:
  #print(line)
  if "http://www.ebi.ac.uk/efo/swo/" in line:
    modified = line.replace("http://www.ebi.ac.uk/efo/swo/", "efoswo:").strip()
    updatedIRI = line.replace("http://www.ebi.ac.uk/efo/swo/", "swo:").strip()
    # place class as child of ObsoleteClass
    outfile.write(modified + " rdfs:subClassOf obo:ObsoleteClass .\n")
    # add deprecated boolean
    outfile.write(modified + " oboInOwl:deprecated true .\n")
    # add version number for deprecation
    outfile.write(modified + " efo:obsoleted_in_version \"1.7\" .\n")
    # suggest replacement
    outfile.write(modified + " obo:IAO_0100001 " + updatedIRI + " .\n")
    outfile.write("\n")
  else:
    print("no match to efo-swo IRI found in swo:" + line)

outfile.write("}")
outfile.write("WHERE { }")

infile.close()
outfile.close()
