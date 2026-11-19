# Application-specific tests

Every application has behaviour the general levels miss, and it is often what the application exists for. Find it while mapping, record it at level `app`, and give it suites of its own.

## Finding it

Ask of the application: what does it do that a plain "create, read, change, delete" service does not? Typical answers, each with what to assert.

**Generated answers** (text produced by a language model, search, recommendations)
- Never assert the exact text. Assert properties: the form (valid JSON, the documented fields, a length within bounds, the right language); that facts from the source the test provided are in the answer, and known-wrong ones are not; that an out-of-scope or unsafe request is declined the way the application defines; that a cited source is one of those provided.
- Use a small, fixed set of inputs with their expected properties, kept in this repository and built on data the tests own.
- Where one run cannot be trusted, run the case several times inside the test and require a stated share to pass ("4 of 5"); write the numbers into the behaviour's `expect`.
- Prefer checks a program can decide: words, patterns, numbers within a tolerance, a schema. Use a second model as a judge only where the project provides one for the purpose.
- Mind the cost: each case may be a paid call. Keep the set small, keep these tests in a suite of their own, and respect what the project notes forbid.

**Background jobs and schedules**
- Start the job the way the application offers (an endpoint, an admin action, a queue message), then wait with a deadline for its effect.
- Run it twice: the effect is there once.
- Give it an input it cannot process: the documented outcome follows, and the other inputs are still processed.
- A job that can only run on its schedule is `blocked`, with what would be needed to start it.

**Data moving between systems** (imports, exports, synchronisation)
- A change at the source arrives at the target: compare counts, and a sample of records field by field.
- Running it again changes nothing. A deletion at the source has the documented effect at the target.
- A malformed record is reported and does not stop the rest.

**Who may do what**
- Build the matrix of roles and actions from the permission rules in the code, and check each cell: allowed means the action succeeds, denied means the documented refusal and no change.
- One tenant's or user's data is never visible to another: create as one, look as the other. Priority 1.

**Files**
- Each accepted type is processed with the documented result; a type or size outside the limits is refused with the documented message.
- What is downloaded equals what was uploaded. A file name with path parts or odd characters is handled safely.

**Messages to people** (email, chat, push)
- Check them only through a test inbox or sink the environment provides. Without one, assert what the application records about the message, and never cause one to reach a real person.

**Limits and switches** (rate limits, quotas, feature flags)
- The request just inside the limit succeeds; the first one outside gets the documented refusal. Do this with the smallest limit available, never by sending load.

## Suites

Keep fast and free cases apart from slow or paid ones: two suites, each with a folder of its own, so the fast one runs with everything else and the costly one on purpose.

- Give the costly one `"on_request": true` in the project map: "every suite" in the playbooks then leaves it out, and it runs only where a playbook names it or the person asks. Say in the README what it costs.
- Both list the level `app`. `qa catalog next` names the first suite in the map that has a behaviour's level, so list the fast one first, and give the behaviours that belong to the costly one the tag `costly`: their tests go into its folder.

## When the expected result is a judgement

If no evidence fixes what "good" means (how relevant is relevant enough), do not invent a threshold and call it intended. Record what the application does today as `observed:`, with the measured numbers, and list it in the report as a decision for the person.
