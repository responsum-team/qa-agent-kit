# B-0000: <what is wrong, in one line>

- **Severity**: critical | major | minor
- **Service**: <name>, at commit <first 12 characters, from `qa status`>
- **Where**: <surface: `POST /v1/orders`, `page /cart`, …>
- **Found**: <date>, run <run id>
- **Test**: `<path>::<test name>` (marked as an expected failure until this is corrected)

## Steps

1. <the state before: who is signed in, which data exists>
2. <the request or the actions, exact enough to repeat; no credentials, no tokens>

```
POST /v1/orders
{"items": []}
```

## Expected

<the outcome, and the evidence that it is intended, with where to find it>

## Actual

<what the application did: status, the relevant part of the body or page, trimmed>

```
200
{"id": "…", "status": "new", "items": []}
```

## Notes

<anything that narrows it down: since which commit, which inputs are affected and which are not; "intent unclear" when a person has to decide whether this is a defect>
