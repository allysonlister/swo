##
## Top-level targets
##

# By default, this Makefile will build the standalone version of the ontology.
build: build/robot.jar merge

# The build_release target should be run with a current release number specified
# with RELNUM, e.g. RELNUM=0.1.0
build_release: check_relinfo clean build add_version

# The release target will create a new release directory locally with all appropriate files.
# Requires RELNUM, PREVIOUS_RELNUM, and DATE
release: copy_release_files

##
## Standalone targets
##

# Compare two OWL files, providing LEFT and RIGHT paths and filenames
robot_diff:
	$(ROBOT) diff --left $(LEFT) --right $(RIGHT) > $(BUILD_DIR)/diff.txt

# Creates a diff between RELNUM and PREVIOUS_RELNUM
diff_release: check_relinfo
	test $(PREVIOUS_RELNUM)
	robot_diff LEFT=$(RELEASE_TOP_DIR)/$(PREVIOUS_RELNUM)/swo-$(PREVIOUS_RELNUM).owl RIGHT=$(RELEASE_FILE)

##
## Variables
##

# ROBOT_REMOTE_LOCATION := https://build.berkeleybop.org/job/robot/lastSuccessfulBuild/artifact/bin/robot.jar
ROBOT_REMOTE_LOCATION := https://github.com/ontodev/robot/releases/download/v1.4.3/robot.jar
ROBOT := java -jar build/robot.jar

# The development file - please note that the filename (though not the directory) is the
# same for the DEV_FILE and BASE_NAME.
BASE_NAME := swo.owl
DEV_FILE := ontology/$(BASE_NAME)

# Where all the files created by this makefile should go
BUILD_DIR := build

# The release directory
RELEASE_DIR := ../release

# The standalone OWL file
MERGE_FILE := $(BUILD_DIR)/swo-merged.owl
# The standalone OWL file with release numbers added
BUILD_RELFILE := $(BUILD_DIR)/$(BASE_NAME)
# The release file in the release directory
RELEASE_FILE := $(RELEASE_DIR)/$(BASE_NAME)

# Various IRIs for the OWL files created within this Makefile
RELEASE_IRI := http://www.ebi.ac.uk/swo/swo.owl
RELEASE_VERSION_IRI := $(RELEASE_IRI)/$(RELNUM)

##
## Targets
##

clean:
	rm -rf build

check_relinfo:
	test $(RELNUM)
	test $(DATE)

check_release_vars:
	test $(PREVIOUS_RELNUM)

check_relfiles: check_relinfo
	test -f $(BUILD_RELFILE)

# All files, including the release files, are put into build/ in case there are
# any issues with the run. They can be be moved to a release directory via the release target
# then committed manually once we're sure they're ok.
reqd_build_dirs:
	mkdir -p $(BUILD_DIR)

# BUILD_DIR must already be present, and the previous release directory must have been made already
reqd_release_dirs:
	test -d $(BUILD_DIR)
	test -d $(RELEASE_DIR)
	test -d $(RELEASE_DIR)/$(PREVIOUS_RELNUM)

build/robot.jar: | reqd_build_dirs
	curl -L -o build/robot.jar $(ROBOT_REMOTE_LOCATION)
	chmod ug+x build/robot.jar

merge:
	$(ROBOT) merge --input $(DEV_FILE) --output $(MERGE_FILE)

add_version : merge
	$(ROBOT) annotate --input $(MERGE_FILE) \
	--ontology-iri $(RELEASE_IRI) \
	--version-iri $(RELEASE_VERSION_IRI) \
	--annotation owl:versionInfo "$(RELNUM)" \
	--annotation rdfs:comment "Date of release: $(DATE)" \
	--output $(BUILD_RELFILE)


copy_release_files: | reqd_release_dirs check_relfiles check_release_vars
	cp $(BUILD_RELFILE) $(RELEASE_DIR)
