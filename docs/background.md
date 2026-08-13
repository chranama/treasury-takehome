# Project Background

## Organizational context

The Alcohol and Tobacco Tax and Trade Bureau (TTB) regulates alcohol labeling at the federal level. Within TTB, the [Alcohol Labeling and Formulation Division (ALFD)](https://www.ttb.gov/about-ttb/who-we-are/offices/alcohol-labeling-and-formulation-division) reviews and processes applications for Certificates of Label Approval or Exemption (COLAs) and works to ensure that alcohol labels comply with federal requirements and provide consumers with adequate product information.

The likely primary user for this prototype is an ALFD label reviewer. The project uses the generic term **label reviewer**.

## Existing review workflow

The [Treasury take-home instructions](https://github.com/treasurytakehome-rgb/instructions) describe a workflow in which a reviewer opens an application, examines the submitted label artwork, and checks whether visible information agrees with the application. Common comparisons include brand name, class or type, alcohol content, net contents, and the mandatory Government Health Warning.

Much of this work is direct comparison, but it is not purely mechanical. Differences in capitalization or typography may be harmless, while missing words, conflicting quantities, unreadable text, or altered warning language require judgment. The tool therefore needs to make routine comparisons quickly without presenting its output as an official approval or rejection.

## Problem described by stakeholders

The assignment's discovery notes identify several connected problems:

- Reviewers spend substantial time performing repetitive application-to-artwork comparisons.
- A previous scanning workflow was too slow to remain useful; stakeholders expect a simple case to return a result in about five seconds.
- Reviewers have widely varying levels of technical comfort, making a clean and obvious interface essential.
- Peak workloads can include groups of 200–300 applications that are otherwise handled one at a time.
- Poor image quality and ambiguous differences require human review rather than confident automated guesses.
- Treasury networks block outbound access to many domains, so browser-side dependencies and direct calls to external model services can make features unavailable.

These concerns favor a focused review aid over an ambitious attempt to automate the entire COLA process.

## Prototype opportunity

The prototype explores whether AI-assisted text extraction, followed by deterministic comparison rules, can reduce routine visual matching while keeping the reviewer in control. For a single review, the user supplies expected application values and label artwork. The application extracts the visible fields, compares them with the expected values, checks the Government Warning, and explains any discrepancy or uncertainty.

The bounded batch workflow demonstrates how the same review model extends to multiple applications. It is a usability and failure-handling demonstration rather than a claim of production-scale throughput.

## Relationship to TTB systems

This project is a standalone proof of concept. It does not integrate with COLAs Online, retrieve real application records, issue a COLA, or reproduce TTB's official review states. A reviewer manually supplies expected values for the demo, and only synthetic or otherwise non-sensitive label data should be used.

The deployed prototype runs outside Treasury's production environment. Restricted outbound network access remains an important design constraint, but this exercise does not establish FedRAMP compliance, Treasury allowlisting, or readiness for a production government deployment.

## Sources

- [Treasury take-home instructions](https://github.com/treasurytakehome-rgb/instructions) — assignment background, stakeholder notes, deliverables, and evaluation criteria.
- [TTB Alcohol Labeling and Formulation Division](https://www.ttb.gov/about-ttb/who-we-are/offices/alcohol-labeling-and-formulation-division) — official organizational context and responsibilities.
- [TTB COLAs Online resources](https://www.ttb.gov/regulated-commodities/labeling/colas) — contextual information about TTB label-approval systems and resources.
