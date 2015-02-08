The folders are organised as follows:

inferred_swo contains a merged version of the Software Ontology which includes inference over the axios, that is, inference has been saved as asserted axioms in this version. This is the version to use in viewing the ontology in applications.

swo_components contains the individual owl modules that make up SWO. the swo_core file imports the others so should be considered the 'main file'. This includes the import of the EDAM ontology into SWO.

swo_merged is a single owl with all of the separate owl modules placed into just one owl file


 ____  _   _  ____    ___  _____  ____  ____  _    _    __    ____  ____ 
(_  _)( )_( )( ___)  / __)(  _  )( ___)(_  _)( \/\/ )  /__\  (  _ \( ___)
  )(   ) _ (  )__)   \__ \ )(_)(  )__)   )(   )    (  /(__)\  )   / )__) 
 (__) (_) (_)(____)  (___/(_____)(__)   (__) (__/\__)(__)(__)(_)\_)(____)
 _____  _  _  ____  _____  __    _____  ___  _  _ 
(  _  )( \( )(_  _)(  _  )(  )  (  _  )/ __)( \/ )
 )(_)(  )  (   )(   )(_)(  )(__  )(_)(( (_-. \  / 
(_____)(_)\_) (__) (_____)(____)(_____)\___/ (__) 


--------------------------------
The Software Ontology

http://theswo.sourceforge.net

RELEASE NOTES

VERSION: 1.5
DATE: 8th February 2015
--------------------------------


--------------------------------
Contents

1. Summary
2. Change log
3. Obsolete Classes
4. Release Policy

--------------------------------



--------------------------------
1. Summary

Release 1.5 contains many new textual definitions, particularly for higher level terms used in classifying subclasses. Several refactoring decisions were made to reconcile some of the EDAM modelling that does not correspond with some of the OBO relations. See the following links for details:

https://softwareontology.wordpress.com/2014/12/09/assorted-modifications-december-2014/
https://softwareontology.wordpress.com/2014/10/16/definition-and-id-changes/


--------------------------------


--------------------------------
2. Change log

If you wish to perform a full diff between these versions please visit:

http://wwww.ebi.ac.uk/efo/bubastis





--------------------------------


--------------------------------
3. Obsolete Classes

None.
--------------------------------


--------------------------------
4. Release Policy

1. Releases of SWO will appear in the sourceforge folder \trunk\src\release\swoinowl

2. Assigned identifiers will remain on a class and will not be deleted from the owl files. classes that are no longer required will be subclassed under the obsolete class and stripped of axioms but the uri will remain.

3. Classes that change their intended meaning significantly (i.e. they denote a new entity) will be assigned a new URI and the old URi will be made obsolete as per the above.

4. A release notes file will be attached to each release. this will appear in the folder trunk\src\release\releasenotes and will outline major changes 
--------------------------------