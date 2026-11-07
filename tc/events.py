"""Event names: the contract between the Telegram bot and the durable workflows."""

MESSAGE_RECEIVED = "tc/message.received"  # data: {message_id}
COMMITMENT_PROPOSED = "tc/commitment.proposed"  # data: {commitment_id}
COMMITMENT_DECIDED = "tc/commitment.decided"  # data: {commitment_id, decision: yes|no}
COMMITMENT_COMPLETED = "tc/commitment.completed"  # data: {commitment_id}
COMMITMENT_CANCELLED = "tc/commitment.cancelled"  # data: {commitment_id}
COMMITMENT_RESCHEDULED = "tc/commitment.rescheduled"  # data: {commitment_id, version}
