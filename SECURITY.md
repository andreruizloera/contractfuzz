# Security

## Reporting

Report vulnerabilities privately to andre.x.ruizloera@gmail.com. Expect an
acknowledgement within a week. Please do not open public issues for
security reports.

## Threat model notes

contractfuzz parses untrusted input: OpenAPI documents you did not write.

- Specs are parsed with `yaml.safe_load`; no object construction.
- `$ref` resolution is internal-only. External references (files or URLs)
  are rejected, so a hostile spec cannot make contractfuzz read arbitrary
  files or make network requests during generation.
- Recursive references are detected and rejected rather than expanded, so
  a spec cannot force unbounded recursion through self-reference.
- Fixture filenames are derived from mutation descriptions but reduced to
  a `[a-z0-9_]` slug, so schema content cannot produce path traversal in
  output filenames. The pytest plugin's test ids come from the same slug,
  so a hostile spec cannot inject shell or pytest syntax into a node id.
- The pytest plugin reads the spec path given in the decorator and nothing
  else. It writes no files and opens no sockets.

The proxy is a development tool. It listens on 127.0.0.1 by default,
forwards to the single upstream you name on the command line, and should
not be exposed to untrusted networks: it performs no authentication and
forwards client headers (minus hop-by-hop headers) as-is.
