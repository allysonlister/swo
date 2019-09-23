# Licence Hierarchy in SWO

Within SWO, software can be related to the licences that describe how that it may be used. The asserted hierarchy is relatively flat, with a simple list of licenses grouped by their owning organizations.

![Licence Hierarchy](images/LicenceHierarchy.png)

In this way, the asserted hierarchy remains a simple, easy-to-read structure. Added semantics are provided by a set of axioms that describe each licence in detail. These axioms relate each license to a hierarchy of licence clauses.

![Licence Clauses](images/LicenceClauses.png)

For example, the CC BY 4.0 licence contains the following axioms:

![CC BY 4.0 Axioms](images/cc-by-4.png)


In addition to the licence classes and licence clause classes, we have created a number of defined classes which will group licenses according their clauses upon inference with a reasoner. This allows multiple inferred hierarchies that group licences according to any number of axes such as open source licences, fee-based licences, and more. Additional defined classes to suit users can be added as required. 

![Example Defined Classes](images/DefinedClasses.png)


Not all licences are included in SWO, but we invite members of the community to contact us via our [Issue Tracker](https://github.com/allysonlister/swo/issues) if they would like additional clauses, licences or groupings added.
