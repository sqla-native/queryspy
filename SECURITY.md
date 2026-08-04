# Security Policy

## Supported versions

The latest released minor version receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[security advisory](https://github.com/sqla-native/queryspy/security/advisories/new)
form rather than opening a public issue.

You can expect an acknowledgement within a few days and an assessment of
severity and remediation timeline shortly after.

## Scope

`queryspy` is a testing and diagnostics library. It reads SQL statement text and
stack frames in the process that imports it, and writes them to the report it
renders. It opens no sockets, spawns no subprocesses, and reads no files.

Note that findings include rendered SQL and source paths. If you forward
queryspy output to an external system, treat it as you would any other debug
output from your application.
