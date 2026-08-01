# Ticket Operations

Tickets retain the existing private-channel boundary. Staff use ticket list, assignment, note, status, and overdue commands. Internal notes are never delivered to customers; customer-visible notes update response timing. Valid states are NEW, OPEN, WAITING_FOR_STAFF, WAITING_FOR_CUSTOMER, ESCALATED, RESOLVED, CLOSED, REOPENED, SPAM, and DUPLICATE.

SLA defaults, departments, and response macros live under `config/discord/`.
