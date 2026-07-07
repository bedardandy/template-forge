# fixtures — synthetic demo corpus

152 fully **synthetic** fixture files, each a flat slot/fact object suitable for
driving the assembly engine. **All data is fictional**: names are drawn from the
Doe / Roe / Poe / Public placeholder families, addresses are in "Anytown", and no
value derives from any real person, client, or matter.

These fixtures demonstrate the *shape* of the slot data different document types
consume (deeds, affidavits, declarations, discovery, leases, notices, advance
directives, powers of attorney, and more). They are a demo corpus, not tied to
any one manifest — build your own manifests (or a private pack) to assemble
against them.

> The framework runs end-to-end on this synthetic corpus with zero proprietary
> bytes. Regenerate additional synthetic matters with
> `python -m template_forge.generator.synthetic`.
