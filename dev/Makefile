##
## Top-level targets
##

# By default, this Makefile will build the standalone version of the ontology.
build: build/robot.jar merge

# The build_release target should be run with a current release number specified
# with RELNUM, e.g. RELNUM=0.1.0
build_release: check_relnum clean build add_version

# The release target will create a new release directory locally with all appropriate files
# other than the refactored IRIs file, which still need to be made manually
release: copy_release_files

##
## Standalone targets
##

# Compare two OWL files, providing LEFT and RIGHT paths and filenames
robot_diff:
	$(ROBOT) diff --left $(LEFT) --right $(RIGHT) > $(BUILD_DIR)/diff.txt

# Creates a diff between RELNUM and PREVIOUS_RELNUM
diff_release: check_relnum
	test $(PREVIOUS_RELNUM)
	robot_diff LEFT=$(RELEASE_TOP_DIR)/$(PREVIOUS_RELNUM)/swo-$(PREVIOUS_RELNUM).owl RIGHT=$(RELEASE_FILE)

##
## Variables
##

# ROBOT_REMOTE_LOCATION := https://build.berkeleybop.org/job/robot/lastSuccessfulBuild/artifact/bin/robot.jar
ROBOT_REMOTE_LOCATION := https://github.com/ontodev/robot/releases/download/v1.4.1/robot.jar
ROBOT := java -jar build/robot.jar

# The development file
DEV_FILE := ontology/swo.owl

# Where all the files created by this makefile should go
BUILD_DIR := build

# The release name
RELEASE_NAME := swo-$(RELNUM).owl
# The release directory
RELEASE_TOP_DIR := ../release
RELEASE_DIR := $(RELEASE_TOP_DIR)/$(RELNUM)

# The standalone OWL file
MERGE_FILE := $(BUILD_DIR)/swo-merged.owl
# The standalone OWL file with release numbers added
BUILD_RELFILE := $(BUILD_DIR)/$(RELEASE_NAME)
# The release file in the release directory
RELEASE_FILE := $(RELEASE_DIR)/$(RELEASE_NAME)

# Various IRIs for the OWL files created within this Makefile
MERGE_IRI := "http://www.ebi.ac.uk/swo/swo-merged.owl"
RELEASE_IRI := "http://www.ebi.ac.uk/swo/swo.owl"
RELEASE_VERSION_IRI := "http://www.ebi.ac.uk/swo/$(RELEASE_NAME)"

##
## Targets
##

clean:
	rm -rf build

check_relnum:
	test $(RELNUM)

check_relfiles: check_relnum
	test -f $(BUILD_RELFILE)

# All files, including the release files, are put into build/ in case there are
# any issues with the run. They can be be moved to a release directory via the release target
# then committed manually once we're sure they're ok.
reqd_build_dirs:
	mkdir -p $(BUILD_DIR)

# BUILD_DIR must already be present, and RELEASE_DIR must not be present yet.
reqd_release_dirs:
	test -d $(BUILD_DIR)
	mkdir $(RELEASE_DIR)

build/robot.jar: | reqd_build_dirs
	curl -L -o build/robot.jar $(ROBOT_REMOTE_LOCATION)
	chmod ug+x build/robot.jar

merge:
	$(ROBOT) merge --input $(DEV_FILE) \
	annotate --ontology-iri $(MERGE_IRI) --output $(MERGE_FILE)

add_version : merge
	$(ROBOT) annotate --input $(MERGE_FILE) \
	--ontology-iri $(RELEASE_IRI) \
	--version-iri $(RELEASE_VERSION_IRI) \
	--output $(BUILD_RELFILE)


copy_release_files: | reqd_release_dirs check_relfiles
	cp $(BUILD_RELFILE) $(RELEASE_DIR)
