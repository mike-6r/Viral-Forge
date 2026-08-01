# Discord permission matrix

| Audience | May view | May send | May access production controls |
| --- | --- | --- | --- |
| Everyone | public information and support entry points | public/community channels | No |
| Member | community channels | community channels | No |
| Verified Customer | customer workspace | customer workspace | No |
| Staff | staff control and support tickets | staff control | No, unless separately configured as an operator |
| Operator | operator control plane | operator control plane | Only with existing allowed-role configuration |
| Discord server owner | setup/admin commands | as Discord permits | Existing controls still require the configured operational role boundary |

Tickets deny `@everyone`, permit the requester, and permit only configured staff roles. Private customer and operator categories deny `@everyone` explicitly.
