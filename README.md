# Alcohol Label Verification Prototype

A standalone proof of concept that helps an alcohol-label reviewer compare expected application information with visible label artwork. The application is intended to reduce routine matching work while leaving ambiguous and regulatory decisions to a human reviewer.

## Project status

The project is currently in planning and initial implementation. Technology-specific setup instructions and final test commands will be added as the implementation is established.

## Planned demo

The core workflow will allow a reviewer to:

1. enter the expected brand name, class/type, alcohol content, and net contents;
2. upload a label image;
3. review extracted values alongside the expected values;
4. check the mandatory Government Health Warning; and
5. identify matches, discrepancies, and cases requiring human review.

A bounded batch workflow is also planned to demonstrate how the same review could be applied to multiple applications.

## Deployed application

**URL:** [https://label-review.mealcheck.dev](https://label-review.mealcheck.dev) is the planned deployed URL; however, the project is not yet deployed.

The submitted deployment will provide the working browser-based prototype without requiring local installation or access to this repository.

## Local setup and run instructions

To be added after the application stack and dependency workflow are initialized.

## Tests

Test commands and fixture instructions will be added with the implementation.

## Documentation

- [Project background](docs/background.md)
- [Demo specification and assumptions](docs/specification.md)
- [Implementation approach, tools, and assumptions](docs/implementation.md)
