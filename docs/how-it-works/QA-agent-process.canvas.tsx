import {
  Divider,
  H1,
  H2,
  H3,
  Row,
  Stack,
  Text,
  useEffect,
  useHostTheme,
  useRef,
  useState,
} from "cursor/canvas";
import type { CSSProperties } from "cursor/canvas";

type Who = "people" | "agent";

type NamedLine = { name: string; text: string };

type Lane = { who: Who; text?: string; lines?: NamedLine[] };

type Stage = {
  title: string;
  tag: string;
  step?: number;
  repeats?: boolean;
  lanes: Lane[];
};

type ChainStep = { title: string; detail: string; who: Who };

type WorkflowStep = { label: string; detail: string; people?: boolean };

type Workflow = {
  name: string;
  when: string;
  stage?: number;
  steps: WorkflowStep[];
};

type ExampleStep = { title: string; detail: string; who: Who };

type EffortBar = { label: string; value: string; hours: number; who: Who };

type EffortGroup = {
  title: string;
  note: string;
  bars: EffortBar[];
  breakdown: string;
};

type Example = {
  title: string;
  story: string;
  steps: ExampleStep[];
  groups: EffortGroup[];
};

const STAGES: Stage[] = [
  {
    step: 1,
    title: "Set up",
    tag: "Once",
    lanes: [
      {
        who: "people",
        text: "Install the kit, answer ten questions and agree the scope.",
      },
      {
        who: "agent",
        text: "Records the project map, checks that every service answers and installs the team's rules.",
      },
    ],
  },
  {
    step: 2,
    title: "Build the baseline",
    tag: "Unattended",
    lanes: [
      {
        who: "agent",
        text: "Lists what the application should do, then writes, runs and verifies a test for each behaviour.",
      },
      {
        who: "people",
        text: "Review the report and merge the QA branches.",
      },
    ],
  },
  {
    step: 3,
    title: "Maintain the tests",
    tag: "Repeats after merges",
    repeats: true,
    lanes: [
      {
        who: "people",
        text: "Merge code and update the running services as usual, then ask for a sync.",
      },
      {
        who: "agent",
        lines: [
          { name: "Sync", text: "adds, updates and retires tests after merges." },
          { name: "Heal", text: "repairs failing and flaky tests." },
          { name: "Review", text: "checks the tests themselves." },
        ],
      },
    ],
  },
];

const SCORE: Stage = {
  title: "Score",
  tag: "Independent check",
  lanes: [
    {
      who: "agent",
      text: "After the baseline, a separate session scores the tests against an existing suite that the test-writing sessions never read.",
    },
    {
      who: "people",
      text: "Check a random sample of the matches by hand.",
    },
  ],
};

const SESSION: ChainStep[] = [
  {
    title: "Read the record",
    detail: "Where the work stands and what is next",
    who: "agent",
  },
  {
    title: "Follow one playbook",
    detail: "Step by step, on a local QA branch",
    who: "agent",
  },
  {
    title: "Guard, then commit",
    detail: "Progress is recorded with every commit",
    who: "agent",
  },
  {
    title: "Report",
    detail: "Decisions, defects, what is blocked",
    who: "agent",
  },
  {
    title: "Review and merge",
    detail: "Nothing is pushed",
    who: "people",
  },
];

const RECORD = [
  "Project map",
  "Agreement",
  "Behaviour catalog",
  "Run history",
  "Sync points",
  "Progress",
  "Reports",
];

const WORKFLOWS: Workflow[] = [
  {
    name: "Set up",
    when: "Once per project",
    stage: 1,
    steps: [
      { label: "Ask", detail: "Ten questions, one message", people: true },
      { label: "Project map", detail: "Services, sign-in, limits" },
      { label: "Credentials", detail: "Typed in by a person", people: true },
      { label: "Check", detail: "Every service answers" },
      { label: "Team rules", detail: "From the team's own library" },
      { label: "Agree the stack", detail: "A person says yes", people: true },
      { label: "Hand over", detail: "First commit, QA branch" },
    ],
  },
  {
    name: "Baseline",
    when: "Once, after setup",
    stage: 2,
    steps: [
      { label: "Map", detail: "Behaviours, with evidence" },
      { label: "Scaffold", detail: "Suites run from one command" },
      { label: "Generate", detail: "A test for each behaviour" },
      { label: "Verify", detail: "Full runs, three in a row" },
      { label: "Report", detail: "What was built and found" },
    ],
  },
  {
    name: "Sync",
    when: "After merges",
    stage: 3,
    steps: [
      { label: "See what changed", detail: "Merges since the last sync" },
      { label: "Decide", detail: "Was each change meant?" },
      { label: "Run and repair", detail: "Every suite, every failure" },
      { label: "Write new tests", detail: "For the added behaviour" },
      { label: "Report", detail: "Changes and findings" },
    ],
  },
  {
    name: "Heal",
    when: "When tests fail or are flaky",
    stage: 3,
    steps: [
      { label: "Take stock", detail: "Run every suite first" },
      { label: "Repair failing", detail: "Fix the test or record a defect" },
      { label: "Cure flaky", detail: "Fix the cause, or quarantine" },
      { label: "Report", detail: "Repairs, defects, quarantines" },
    ],
  },
  {
    name: "Review",
    when: "Best in a fresh session",
    stage: 3,
    steps: [
      { label: "Mechanical checks", detail: "Guard, catalog, coverage" },
      { label: "Read the tests", detail: "Would each catch a break?" },
      { label: "Find gaps", detail: "Critical paths, error cases" },
      { label: "Act", detail: "Strengthen, correct, add" },
      { label: "Report", detail: "Findings, what was done" },
    ],
  },
  {
    name: "Score",
    when: "A separate session",
    steps: [
      { label: "Extract", detail: "Tests of both suites" },
      { label: "Judge", detail: "A verdict per reference test" },
      { label: "Report", detail: "Share replicated vs threshold" },
      { label: "Hand check", detail: "A person checks a sample", people: true },
    ],
  },
];

const PEOPLE_DECIDE = [
  "The scope, the test stack and where tests may be written. Agreed once at setup, changed only when a person says so.",
  "The credentials. A person types them into a file the agent never opens.",
  "What gets merged. The work stays on local branches until someone reviews it.",
];

const LIMITS = [
  "No change to application code.",
  "No push, merge or pull request.",
  "No secret written into a file.",
  "No test loosened to make it pass. A correct test that fails is reported as a defect.",
  "No reading of folders named as off limits.",
];

const GUARD_CHECKS = [
  "Files were written only where agreed.",
  "The services' own checkouts are unchanged.",
  "No credential value is about to be committed.",
  "No test was switched off without a recorded reason.",
];

const ESTIMATE_NOTE =
  "Rough planning estimates, not measurements: the kit has not yet been run on a real project. They assume setup and the baseline are done, and count a person's working time only. The assistant's own running cost is not included, and nor is updating the running service, which a person does either way.";

const FEATURE_EXAMPLE: Example = {
  title: "In practice: a new feature is tested",
  story:
    "In Example Shop, an invented online shop, the developers merge a refund feature. No test covers it yet.",
  steps: [
    {
      who: "people",
      title: "Merge and ask for a sync",
      detail:
        "The running service is updated as usual, then a person says \"sync\".",
    },
    {
      who: "agent",
      title: "See what changed",
      detail: "A new refund operation that no recorded behaviour covers.",
    },
    {
      who: "agent",
      title: "Record the behaviours",
      detail:
        "Nine cases, each with its evidence: a refund, too much, twice, another customer's order, and so on.",
    },
    {
      who: "agent",
      title: "Write and run the tests",
      detail:
        "One test for each case, then every suite in full to check that nothing else broke.",
    },
    {
      who: "agent",
      title: "Guard, commit, report",
      detail: "On a local QA branch. The report lists what was added and found.",
    },
    {
      who: "people",
      title: "Review and merge",
      detail: "Read the report and the tests. Nothing is pushed.",
    },
  ],
  groups: [
    {
      title: "Without the process",
      note: "A person does it by hand",
      bars: [
        { who: "people", label: "A person", value: "about 6 h", hours: 6 },
      ],
      breakdown:
        "Read the change and plan the cases 1 h · write and fix nine tests 3–4 h · run everything and sort out failures 1 h · write up the result ½ h.",
    },
    {
      title: "With the process",
      note: "The agent does the work; a person asks and reviews",
      bars: [
        { who: "people", label: "A person", value: "about 1 h", hours: 1 },
        {
          who: "agent",
          label: "The agent, unattended",
          value: "1–2 h",
          hours: 1.5,
        },
      ],
      breakdown:
        "The person asks for the sync (minutes) and reviews the report and the nine tests (about 1 h). The agent's time depends mostly on how long the suites take to run.",
    },
  ],
};

const BUGFIX_EXAMPLE: Example = {
  title: "In practice: a bugfix is tested",
  story:
    "Earlier, the agent found that Example Shop accepts an empty cart instead of refusing it. It recorded a defect and kept a test that expects the refusal, marked as a known failure. Now the developers merge the fix.",
  steps: [
    {
      who: "people",
      title: "Merge and ask for a sync",
      detail:
        "The fix is merged, the running service updated, and a person says \"sync\".",
    },
    {
      who: "agent",
      title: "See what changed, and decide",
      detail:
        "The fix touches the code behind the recorded defect, and its commit says empty carts are now refused.",
    },
    {
      who: "agent",
      title: "Run every suite",
      detail:
        "The waiting test now passes, and the run flags it. A test that the fix broke would show up as a new failure.",
    },
    {
      who: "agent",
      title: "Clear the known failure",
      detail:
        "Remove the marker and link the test again. The next run records it as passing.",
    },
    {
      who: "agent",
      title: "Guard, commit, report",
      detail:
        "On a local QA branch. The report lists what changed in the tests.",
    },
    {
      who: "people",
      title: "Review and merge",
      detail: "Read the report and check the small change to the test.",
    },
  ],
  groups: [
    {
      title: "Without the process",
      note: "A person does it by hand",
      bars: [
        {
          who: "people",
          label: "A person",
          value: "1½–2 h",
          hours: 1.75,
        },
      ],
      breakdown:
        "Find the bug report and its steps 15 min · write a test that stops the bug coming back 30–60 min · run everything and sort out failures 30 min · note the result 15 min. Checking by hand only takes about 15 min, but nothing then stops the bug coming back.",
    },
    {
      title: "With the process",
      note: "The agent does the work; a person asks and reviews",
      bars: [
        {
          who: "people",
          label: "A person",
          value: "about 15 min",
          hours: 0.25,
        },
        {
          who: "agent",
          label: "The agent, unattended",
          value: "30–60 min",
          hours: 0.75,
        },
      ],
      breakdown:
        "The person asks for the sync (minutes) and reviews the report and the small change to the test (about 15 min). The agent spends most of its time running the suites.",
    },
  ],
};

const tight: CSSProperties = { margin: 0 };

const STEP_COLUMNS =
  "repeat(auto-fill, minmax(max(140px, calc(100% / 7 - 0.5px)), 1fr))";

function useElementWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    const measure = () => setWidth(element.getBoundingClientRect().width);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return { ref, width };
}

function Dot({ who, top = 0 }: { who: Who; top?: number }) {
  const theme = useHostTheme();
  const people = who === "people";
  return (
    <span
      aria-hidden="true"
      style={{
        flex: "none",
        boxSizing: "border-box",
        width: 8,
        height: 8,
        marginTop: top,
        borderRadius: theme.radius.full,
        background: people ? theme.category.purple : "transparent",
        border: people ? "none" : `1.5px solid ${theme.text.tertiary}`,
      }}
    />
  );
}

function WhoLabel({ who }: { who: Who }) {
  const theme = useHostTheme();
  const people = who === "people";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Dot who={who} />
      <Text
        size="small"
        weight="medium"
        style={{
          margin: 0,
          color: people ? theme.category.purple : theme.text.secondary,
        }}
      >
        {people ? "People" : "QA agent"}
      </Text>
    </div>
  );
}

function PersonLegend() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Dot who="people" />
      <Text size="small" tone="secondary" style={tight}>
        A person acts
      </Text>
    </div>
  );
}

function Arrow({ down }: { down?: boolean }) {
  const theme = useHostTheme();
  return (
    <svg
      width="20"
      height="12"
      viewBox="0 0 20 12"
      fill="none"
      aria-hidden="true"
      style={{ flex: "none", transform: down ? "rotate(90deg)" : undefined }}
    >
      <path
        d="M1 6h17"
        stroke={theme.text.tertiary}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M14 2l4 4-4 4"
        stroke={theme.text.tertiary}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function RepeatGlyph() {
  const theme = useHostTheme();
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke={theme.text.tertiary}
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flex: "none" }}
    >
      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
    </svg>
  );
}

function StepBadge({ step }: { step: number }) {
  const theme = useHostTheme();
  return (
    <span
      style={{
        flex: "none",
        width: 22,
        height: 22,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: theme.radius.full,
        background: theme.fill.secondary,
        color: theme.text.primary,
        fontSize: theme.typography.small.fontSize,
        lineHeight: theme.typography.small.lineHeight,
        fontWeight: 600,
      }}
    >
      {step}
    </span>
  );
}

function Connector({ horizontal }: { horizontal: boolean }) {
  return (
    <div
      aria-hidden="true"
      style={{
        flex: "none",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: horizontal ? 36 : undefined,
        height: horizontal ? undefined : 32,
      }}
    >
      <Arrow down={!horizontal} />
    </div>
  );
}

function AsideDivider({ horizontal }: { horizontal: boolean }) {
  const theme = useHostTheme();
  const line = `1px dashed ${theme.stroke.primary}`;
  return (
    <div
      aria-hidden="true"
      style={{
        flex: "none",
        display: "flex",
        alignItems: "stretch",
        justifyContent: "center",
        width: horizontal ? 36 : undefined,
        height: horizontal ? undefined : 32,
      }}
    >
      <div
        style={
          horizontal
            ? { width: 0, borderLeft: line }
            : { flex: 1, height: 0, alignSelf: "center", borderTop: line }
        }
      />
    </div>
  );
}

function StageBox({
  stage,
  horizontal,
  aside,
}: {
  stage: Stage;
  horizontal: boolean;
  aside?: boolean;
}) {
  const theme = useHostTheme();
  return (
    <div
      style={{
        flex: horizontal ? "1 1 0" : "none",
        minWidth: 0,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: 14,
        border: aside
          ? `1px dashed ${theme.stroke.primary}`
          : `1px solid ${theme.stroke.secondary}`,
        borderRadius: theme.radius.lg,
        background: theme.bg.elevated,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {stage.step !== undefined && <StepBadge step={stage.step} />}
          <H3 style={tight}>{stage.title}</H3>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {stage.repeats && <RepeatGlyph />}
          <Text size="small" tone="tertiary" style={tight}>
            {stage.tag}
          </Text>
        </div>
      </div>
      {stage.lanes.map((lane) => (
        <div
          key={lane.who}
          style={{ display: "flex", flexDirection: "column", gap: 2 }}
        >
          <WhoLabel who={lane.who} />
          {lane.text !== undefined && <Text style={tight}>{lane.text}</Text>}
          {lane.lines?.map((line) => (
            <Text key={line.name} style={tight}>
              <Text as="span" weight="semibold">
                {line.name}
              </Text>{" "}
              {line.text}
            </Text>
          ))}
        </div>
      ))}
    </div>
  );
}

function ProcessFlow({ horizontal }: { horizontal: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: horizontal ? "row" : "column",
        alignItems: "stretch",
      }}
    >
      {STAGES.flatMap((stage, index) => [
        ...(index > 0
          ? [<Connector key={`to-${stage.title}`} horizontal={horizontal} />]
          : []),
        <StageBox key={stage.title} stage={stage} horizontal={horizontal} />,
      ])}
      <AsideDivider horizontal={horizontal} />
      <StageBox stage={SCORE} horizontal={horizontal} aside />
    </div>
  );
}

function ChainBox({
  step,
  horizontal,
}: {
  step: ChainStep;
  horizontal: boolean;
}) {
  const theme = useHostTheme();
  return (
    <div
      style={{
        flex: horizontal ? "1 1 0" : "none",
        minWidth: 0,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "10px 12px",
        border: `1px solid ${theme.stroke.secondary}`,
        borderRadius: theme.radius.md,
        background: theme.bg.elevated,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
        {step.who === "people" && <Dot who="people" top={6} />}
        <Text weight="semibold" style={tight}>
          {step.title}
        </Text>
      </div>
      <Text size="small" tone="secondary" style={tight}>
        {step.detail}
      </Text>
    </div>
  );
}

function SessionChain({ horizontal }: { horizontal: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: horizontal ? "row" : "column",
        alignItems: "stretch",
      }}
    >
      {SESSION.flatMap((step, index) => [
        ...(index > 0
          ? [<Connector key={`to-${step.title}`} horizontal={horizontal} />]
          : []),
        <ChainBox key={step.title} step={step} horizontal={horizontal} />,
      ])}
    </div>
  );
}

function RecordBand() {
  const theme = useHostTheme();
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "baseline",
        columnGap: 12,
        rowGap: 2,
        padding: "10px 12px",
        borderRadius: theme.radius.md,
        background: theme.fill.quaternary,
      }}
    >
      <Text size="small" weight="semibold" style={tight}>
        The record in the repository
      </Text>
      <Text size="small" tone="secondary" style={tight}>
        {RECORD.join(" · ")}
      </Text>
    </div>
  );
}

function StepBox({ step, last }: { step: WorkflowStep; last: boolean }) {
  const theme = useHostTheme();
  return (
    <div
      style={{
        flex: "1 1 0",
        minWidth: 0,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "8px 10px",
        border: `1px solid ${theme.stroke.secondary}`,
        borderRadius: theme.radius.md,
        background: last ? theme.fill.tertiary : theme.bg.elevated,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
        {step.people && <Dot who="people" top={6} />}
        <Text weight="medium" style={tight}>
          {step.label}
        </Text>
      </div>
      <Text size="small" tone="secondary" style={tight}>
        {step.detail}
      </Text>
    </div>
  );
}

function WorkflowRow({ workflow }: { workflow: Workflow }) {
  const lastIndex = workflow.steps.length - 1;
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "flex-start",
        columnGap: 16,
        rowGap: 8,
      }}
    >
      <div
        style={{
          flex: "0 0 176px",
          display: "flex",
          alignItems: "flex-start",
          gap: 8,
          paddingTop: 8,
        }}
      >
        <div
          style={{
            flex: "none",
            width: 22,
            display: "flex",
            justifyContent: "center",
          }}
        >
          {workflow.stage !== undefined && <StepBadge step={workflow.stage} />}
        </div>
        <div
          style={{
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <Text weight="semibold" style={tight}>
            {workflow.name}
          </Text>
          <Text size="small" tone="secondary" style={tight}>
            {workflow.when}
          </Text>
        </div>
      </div>
      <div
        style={{
          flex: "1 1 420px",
          minWidth: 0,
          display: "grid",
          gridTemplateColumns: STEP_COLUMNS,
          rowGap: 8,
        }}
      >
        {workflow.steps.map((step, index) => (
          <div
            key={step.label}
            style={{ display: "flex", alignItems: "stretch", minWidth: 0 }}
          >
            <StepBox step={step} last={index === lastIndex} />
            <div
              aria-hidden="true"
              style={{
                flex: "none",
                width: 24,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {index < lastIndex && <Arrow />}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PointList({ items, who }: { items: string[]; who?: Who }) {
  const theme = useHostTheme();
  return (
    <ul
      style={{
        listStyle: "none",
        margin: 0,
        padding: 0,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      {items.map((item) => (
        <li
          key={item}
          style={{ display: "flex", alignItems: "flex-start", gap: 8 }}
        >
          <span
            aria-hidden="true"
            style={{
              flex: "none",
              width: 4,
              height: 4,
              marginTop: 8,
              borderRadius: theme.radius.full,
              background:
                who === "people" ? theme.category.purple : theme.text.tertiary,
            }}
          />
          <Text style={tight}>{item}</Text>
        </li>
      ))}
    </ul>
  );
}

function WhoLegend() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
      {(["people", "agent"] as Who[]).map((who) => (
        <div
          key={who}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <Dot who={who} />
          <Text size="small" tone="secondary" style={tight}>
            {who === "people" ? "A person acts" : "The QA agent acts"}
          </Text>
        </div>
      ))}
    </div>
  );
}

function Timeline({ steps }: { steps: ExampleStep[] }) {
  const theme = useHostTheme();
  const lastIndex = steps.length - 1;
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {steps.map((step, index) => (
        <div key={step.title} style={{ display: "flex", gap: 12 }}>
          <div
            style={{
              flex: "none",
              width: 12,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
            }}
          >
            <Dot who={step.who} top={6} />
            {index < lastIndex && (
              <div
                style={{
                  flex: 1,
                  width: 1,
                  marginTop: 4,
                  background: theme.stroke.primary,
                }}
              />
            )}
          </div>
          <div
            style={{
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              gap: 2,
              paddingBottom: index < lastIndex ? 12 : 0,
            }}
          >
            <Text weight="semibold" style={tight}>
              {step.title}
            </Text>
            <Text size="small" tone="secondary" style={tight}>
              {step.detail}
            </Text>
          </div>
        </div>
      ))}
    </div>
  );
}

function EffortBarRow({ bar, max }: { bar: EffortBar; max: number }) {
  const theme = useHostTheme();
  const people = bar.who === "people";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Dot who={bar.who} />
          <Text size="small" tone="secondary" style={tight}>
            {bar.label}
          </Text>
        </div>
        <Text size="small" weight="semibold" style={tight}>
          {bar.value}
        </Text>
      </div>
      <div
        style={{
          height: 10,
          borderRadius: theme.radius.sm,
          background: theme.fill.quaternary,
        }}
      >
        <div
          style={{
            width: `${Math.max(2, (bar.hours / max) * 100)}%`,
            height: "100%",
            boxSizing: "border-box",
            borderRadius: theme.radius.sm,
            background: people ? theme.category.purple : "transparent",
            border: people ? "none" : `1.5px solid ${theme.text.tertiary}`,
          }}
        />
      </div>
    </div>
  );
}

function EffortPanel({ groups }: { groups: EffortGroup[] }) {
  const theme = useHostTheme();
  const max = Math.max(...groups.flatMap((group) => group.bars.map((bar) => bar.hours)));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {groups.map((group, index) => (
        <div
          key={group.title}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            paddingTop: index > 0 ? 14 : 0,
            borderTop:
              index > 0 ? `1px solid ${theme.stroke.tertiary}` : "none",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <Text weight="semibold" style={tight}>
              {group.title}
            </Text>
            <Text size="small" tone="tertiary" style={tight}>
              {group.note}
            </Text>
          </div>
          {group.bars.map((bar) => (
            <EffortBarRow key={bar.label} bar={bar} max={max} />
          ))}
          <Text size="small" tone="secondary" style={tight}>
            {group.breakdown}
          </Text>
        </div>
      ))}
    </div>
  );
}

function ExampleSection({ example }: { example: Example }) {
  const theme = useHostTheme();
  const card = (grow: number): CSSProperties => ({
    flex: `${grow} 1 360px`,
    minWidth: 0,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 12,
    padding: 14,
    border: `1px solid ${theme.stroke.secondary}`,
    borderRadius: theme.radius.lg,
    background: theme.bg.elevated,
  });
  return (
    <Stack gap={12}>
      <Stack gap={4}>
        <Row gap={12} align="center" justify="space-between" wrap>
          <H2>{example.title}</H2>
          <WhoLegend />
        </Row>
        <Text tone="secondary" style={{ maxWidth: 820 }}>
          {example.story}
        </Text>
      </Stack>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "stretch",
          gap: 16,
        }}
      >
        <div style={card(1.15)}>
          <H3 style={tight}>What happens</H3>
          <Timeline steps={example.steps} />
        </div>
        <div style={card(1)}>
          <H3 style={tight}>The effort, roughly</H3>
          <EffortPanel groups={example.groups} />
        </div>
      </div>
      <Text size="small" tone="secondary" style={{ maxWidth: 960 }}>
        {ESTIMATE_NOTE}
      </Text>
    </Stack>
  );
}

export default function QaAgentProcess() {
  const { ref, width } = useElementWidth();
  const measured = width > 0;
  const processHorizontal = !measured || width >= 960;
  const sessionHorizontal = !measured || width >= 760;

  return (
    <div ref={ref} style={{ maxWidth: 1240, margin: "0 auto" }}>
      <Stack gap={32}>
        <Stack gap={8}>
          <H1>How the QA agent works</H1>
          <Text tone="secondary" style={{ maxWidth: 820 }}>
            The kit turns a coding assistant into a QA agent. It builds a
            project's automated tests, from unit tests to end-to-end journeys,
            and brings them up to date after code is merged. People agree the
            scope once and merge every result.
          </Text>
        </Stack>

        <Stack gap={12}>
          <H2>From setup to a maintained test suite</H2>
          <ProcessFlow horizontal={processHorizontal} />
        </Stack>

        <Stack gap={12}>
          <Stack gap={4}>
            <Row gap={12} align="center" justify="space-between" wrap>
              <H2>How one session runs</H2>
              <PersonLegend />
            </Row>
            <Text tone="secondary">
              Stages 1 to 3 run in sessions like this one. Each starts from the
              record kept in the repository, so any assistant can continue
              where the last one stopped.
            </Text>
          </Stack>
          <SessionChain horizontal={sessionHorizontal} />
          <RecordBand />
        </Stack>

        <Divider />

        <Stack gap={16}>
          <Stack gap={4}>
            <Row gap={12} align="center" justify="space-between" wrap>
              <H2>The six workflows, step by step</H2>
              <PersonLegend />
            </Row>
            <Text tone="secondary">
              Each workflow is a playbook the assistant follows. Numbers match
              the stages above.
            </Text>
          </Stack>
          <Stack gap={12}>
            {WORKFLOWS.map((workflow) => (
              <WorkflowRow key={workflow.name} workflow={workflow} />
            ))}
          </Stack>
        </Stack>

        <Divider />

        <ExampleSection example={FEATURE_EXAMPLE} />

        <ExampleSection example={BUGFIX_EXAMPLE} />

        <Divider />

        <Stack gap={16}>
          <H2>What keeps it in check</H2>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 24,
            }}
          >
            <Stack gap={8}>
              <H3>People decide</H3>
              <PointList items={PEOPLE_DECIDE} who="people" />
            </Stack>
            <Stack gap={8}>
              <H3>Limits it works under</H3>
              <PointList items={LIMITS} />
            </Stack>
            <Stack gap={8}>
              <H3>The guard checks, before every commit</H3>
              <PointList items={GUARD_CHECKS} />
              <Text size="small" tone="secondary">
                The guard is a check the agent runs, not a sandbox. Review its
                branches before merging.
              </Text>
            </Stack>
          </div>
        </Stack>
      </Stack>
    </div>
  );
}
