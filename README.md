# skills

This repository contains reusable utility skills. Organization routing and policy
ownership live outside the utility-skill layer.

Workflow skills that need organization decisions consume an explicit,
caller-supplied Saihai task context or a typed artifact such as a `Branch Plan`,
`Task Change Manifest`, or `Publication Manifest`. They validate and execute that
input; they do not select roles, approval owners, review providers, routing, or
publication ownership. If required context is missing, the skill stops and returns
a typed missing-context result to the caller.
