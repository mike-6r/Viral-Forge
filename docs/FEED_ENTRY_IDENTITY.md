# Feed entry identity and ordering

Identity is scoped to a feed subscription and prefers a bounded RSS GUID/Atom ID, then normalized entry URL, then a SHA-256 fingerprint of bounded fields. The database unique constraint prevents repeat identities from creating duplicate content.

After parsing, eligible entries are sorted deterministically by published time, then updated time, then original feed position, then stable identity. Missing dates sort after dated entries and remain limited; provider order is not independently verifiable. Imported content carries source provenance and enters `SOURCE_VERIFICATION_REQUIRED`, so feed discovery is not rights approval.
