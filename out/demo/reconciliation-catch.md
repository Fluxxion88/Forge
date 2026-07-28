# The reconciliation catch

Anvil's fill endpoint fails silently: a value posted to an alias the template does
not have is dropped — no error, and the returned PDF looks complete with one empty
box. On a real filing that is a rejection and another month of a family's life.

`forge fill --via anvil` therefore reconciles first, in both directions, and
refuses to fill on any drift.

Status: verified against a stub transport (tests/test_anvil.py::
test_fill_refuses_on_missing_alias — asserts no fill request is even sent).
LIVE demonstration pending ANVIL_API_KEY; once present:

    forge anvil-register ca-dmv-dl142          # registers cast with our aliases
    forge fill ca-dmv-dl142 --estate estate-02-ca-intestate-independent-admin --via anvil
    # then deliberately break one alias in the draft, re-register, and watch the
    # fill REFUSE rather than return a clean-looking PDF with a hole.
