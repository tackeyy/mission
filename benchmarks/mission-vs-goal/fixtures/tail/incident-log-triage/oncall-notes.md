# On-call notes (raw, unverified)

- Pager fired at 02:18 for checkout error rate.
- First guess in the channel: "the 01:50 deploy broke checkout" — nobody has
  verified what that deploy actually contained.
- Someone also pointed at the clock skew warnings; note they have appeared
  every night this week without customer impact.
- DB team says pool limit is 40 per the capacity doc and was not changed
  tonight.
- The reindex job ran fine last month, but last month it ran at 04:00, not
  02:00, and checkout traffic at 04:00 is near zero.
- Payments vendor status page shows green all night.
