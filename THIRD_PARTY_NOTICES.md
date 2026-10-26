# Third-party material

The XML files in `examples` originate in the KoSIT XRechnung testsuite,
licensed under Apache License 2.0. See `LICENSE`.

Source repository: https://github.com/itplr-kosit/xrechnung-testsuite

* `valid-ubl.xml`: `src/test/business-cases/standard/01.01a-INVOICE_ubl.xml`, Git blob `b0dd3fde1224947d6b224bf41c1feb7680c4ad83`.
* `valid-cii.xml`: `src/test/business-cases/standard/01.01a-INVOICE_uncefact.xml`, Git blob `4ddca14baf40a3e5adcdc7589616a13890fd4820`.
* `relative-attachment-ubl.xml`: modified from the UBL fixture by adding an AdditionalDocumentReference with the synthetic relative URI `relative-path.pdf`. This deliberate test mutation exposes BR-TMP-2 severity escalation. It is not a production invoice.

KoSIT Validator and the two XRechnung configuration packs are downloaded
unmodified at runtime from official release assets and are not bundled in
this source distribution. Their upstream license notices remain in those
distributions. Sources and SHA-256 pins appear in `toolchain.lock.json`.

https://github.com/itplr-kosit/validator

https://github.com/itplr-kosit/validator-configuration-xrechnung

This is an independent project, without KoSIT endorsement or affiliation.
