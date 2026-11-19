# Integration tests

An integration test checks something that only exists when parts work together: two services, or a service with its real database, queue or file store. It acts on one side and observes on the other, through public interfaces.

## How

- **Act through one service, observe through another.** Place an order through the orders API; read the invoice through the billing API.
- **Wait for the effect with a deadline.** Work that crosses services is often finished a moment later. Poll the observing side until the expected state is there or the deadline passes; never pause for a fixed time. The failure message says what was waited for and for how long.
- **Assert the effect once.** After the expected state appears, check that it appeared once: no duplicate record, no second message.
- **Carry identity through.** Where a user or tenant passes from one service to the next, assert that the second service recorded the right one.
- **Clean up on every side**, in the reverse order of creation.

Only public interfaces are used by default. Reading a database or a queue directly is allowed only when the project map provides the connection for tests, and then only to read.

## Cases worth a test

- The main effect of each call between services: the action in one, the state in the other.
- What the calling service does when the other refuses the request (an unknown reference, a rule on the other side), where its code handles that.
- The same event delivered twice, where the code is written to cope with it.
- Data that must agree on both sides after a change: totals, statuses, counts.

## What this level cannot do here

Stopping, slowing or breaking a service or its infrastructure is outside the limits. A behaviour that needs the other side to be down is checked inside the calling service with a fake (level `component`), or recorded as `blocked` with what would be needed.

## When it fails

A failure here has more possible causes than at any other level. Put what identifies the case (the identifiers created, a correlation or request id if the services return one) into the assertion message, so the cause can be found in the services' logs by the person who has them.
