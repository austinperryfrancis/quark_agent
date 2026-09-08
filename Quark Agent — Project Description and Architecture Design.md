# Quark Agent

## Project Description and Architecture Design

**Project Name:** Quark Agent  
**Primary Platform:** Raspberry Pi / low-resource local computers  
**Primary Model Type:** Small local language models, approximately 0.5B–4B parameters  
**Initial MVP:** Autonomous organization and classification of an Obsidian vault  
**Long-Term Goal:** A modular local personal agent capable of acquiring new skills without requiring a larger general-purpose model

---

# 1. Executive Summary

Quark Agent is a lightweight, modular agent framework designed specifically for small local language models running on resource-constrained hardware such as a Raspberry Pi.

The central premise of Quark Agent is that many tasks commonly considered "complex agent tasks" are not inherently composed of complex reasoning at every stage. Instead, they consist of many smaller operations, most of which can be handled deterministically or reduced to narrowly scoped semantic decisions.

Traditional AI agents typically place the language model at the center of the system. The model receives the user's goal, decides what to do, chooses tools, interprets results, maintains context, recovers from errors, and determines when the task is complete.

This architecture works well with large frontier models because those models possess enough reasoning capability to perform all of these functions simultaneously.

Small local models struggle with this architecture.

Quark Agent therefore reverses the relationship.

In Quark Agent:

**The harness is the agent.**

The language model is a specialized semantic reasoning component used only when ordinary software cannot make the required decision reliably.

Quark Agent's runtime is responsible for:

- task decomposition,
- capability discovery,
- context construction,
- state management,
- workflow execution,
- validation,
- retries,
- error recovery,
- safety rules,
- and completion detection.

The local language model receives narrowly defined tasks with minimal context and constrained outputs.

Instead of asking a 1B model:

> Organize my Obsidian vault, determine what all of my notes are about, add appropriate tags, find related notes, create links, and standardize their metadata.

Quark Agent may ask the model:

> Which three tags best describe this note?

Then:

> Which project does this note belong to?

Then:

> Which of these five candidate notes is most closely related?

Each answer is validated before being committed.

This architecture is designed to trade model size for orchestration, repeated verification, deterministic software, and structured workflows.

---

# 2. Core Philosophy

Quark Agent should be built around one fundamental rule:

> **Never ask the language model to solve something that ordinary software can solve, and never ask the language model a question more complicated than necessary.**

The model should not function as a general-purpose controller.

Instead, it should be treated as a fuzzy semantic processor.

Examples of tasks appropriate for the model include:

- classifying text,
- identifying topics,
- extracting concepts,
- choosing among a small number of alternatives,
- generating tags,
- comparing short pieces of text,
- summarizing content,
- identifying relationships,
- interpreting ambiguous natural language.

Examples of tasks that should not require the model include:

- checking whether a file exists,
- reading a file,
- writing a file,
- determining whether YAML parses correctly,
- checking whether a note link points to an existing file,
- calculating hashes,
- detecting duplicate tags,
- parsing dates,
- sorting lists,
- querying SQLite,
- checking command exit codes,
- enforcing permissions,
- detecting whether a previous step succeeded.

Quark Agent should always prefer:

**deterministic logic → small-model reasoning → larger or more expensive reasoning**

rather than using the model for everything.

---

# 3. Initial MVP

The first Quark Agent MVP will be an **Obsidian Note Intelligence Agent**.

Its job is to process notes and improve their organization automatically.

The MVP will have three primary semantic capabilities.

## 3.1 Tag Generation

Quark Agent analyzes each note and determines appropriate tags.

For example:

```text
Meeting with advisor.

Need to rerun the sanctions regressions separating critical
and non-critical imports. Also test whether hub-country imports
increase following sanctions.
```

The agent might propose:

```yaml
tags:
  - sanctions
  - research
  - regressions
  - supply-chain
  - advisor-meeting
```

Tags should preferably be selected from an existing vault taxonomy whenever possible.

The system should avoid uncontrolled tag proliferation such as:

```text
sanctions
sanction
sanctions-paper
sanctions-project
economic-sanctions
```

when these terms represent the same conceptual category.

Therefore Quark Agent should maintain knowledge of existing tags and favor reuse.

---

# 3.2 Related-Note Discovery

Quark Agent should identify notes that are semantically related.

For example:

```text
Meeting with advisor - September 7
```

may be related to:

```text
Sanctions Paper Research Plan
Hub Country Analysis
Critical Inputs Regression Design
Business Groups Sanctions Literature
```

Rather than asking the model to compare every note against every other note, Quark Agent should first retrieve a small set of candidate notes.

Candidate discovery might use:

- keyword search,
- SQLite full-text search,
- BM25,
- embeddings,
- existing tags,
- project membership,
- title similarity,
- recently accessed notes.

The language model then performs the final semantic comparison among perhaps 3–10 candidate notes.

The result may produce Obsidian links such as:

```markdown
## Related Notes

- [[Sanctions Paper Research Plan]]
- [[Hub Country Analysis]]
- [[Critical Inputs Regression Design]]
```

or inline links where appropriate.

---

# 3.3 YAML Frontmatter Generation

Quark Agent should generate or standardize YAML frontmatter for notes.

A note might begin with:

```yaml
---
title: Meeting with Advisor - Sanctions Paper
type: meeting-note
project: sanctions-paper
created: 2026-09-07
tags:
  - sanctions
  - research
  - regressions
status: active
action_required: true
---
```

The exact schema should be configurable.

Quark Agent should not allow the language model to directly write arbitrary YAML into the vault without validation.

Instead:

1. The model returns structured fields.
2. Quark Agent validates each field.
3. Quark Agent serializes the YAML itself.
4. The resulting YAML is parsed again.
5. Only then is the file modified.

This prevents malformed YAML from corrupting notes.

---

# 4. Long-Term Vision

The Obsidian system is only Quark Agent's first skill.

Quark Agent should eventually become a general local personal assistant composed of installable capability modules.

Possible future skills include:

```text
calendar
email
web-search
filesystem
research
home-automation
reminders
contacts
RSS
document-processing
local-search
system-monitoring
coding
task-management
weather
media-management
```

Examples of future user requests might include:

> Move my dentist appointment tomorrow afternoon to an open time after 3 PM.

> Find my notes related to tomorrow's advisor meeting.

> Search the web for updates on this paper and attach useful references to my research note.

> Look through today's inbox and turn anything actionable into tasks.

> Organize the PDFs in my research downloads folder.

> Tell me what changed in my projects since yesterday.

The important architectural requirement is that adding these abilities should **not require rewriting the Quark Agent core**.

Capabilities should behave like plugins.

---

# 5. Architectural Overview

At a high level:

```text
                         USER
                          |
                          v
                 +------------------+
                 | Input Interface  |
                 +--------+---------+
                          |
                          v
                 +------------------+
                 | Intent Router    |
                 +--------+---------+
                          |
                          v
                 +------------------+
                 | Capability       |
                 | Registry         |
                 +--------+---------+
                          |
                          v
                 +------------------+
                 | Task Compiler    |
                 +--------+---------+
                          |
                          v
                 +------------------+
                 | Task Graph / IR  |
                 +--------+---------+
                          |
                          v
                 +------------------+
                 | Scheduler        |
                 +--------+---------+
                          |
             +------------+-------------+
             |            |             |
             v            v             v
        Deterministic   Local LLM      Tools
           Logic        Semantic       Skills
             |            |             |
             +------------+-------------+
                          |
                          v
                 +------------------+
                 | Validator        |
                 +--------+---------+
                          |
                    PASS / FAIL
                     /        \
                    v          v
               Commit      Recovery
                    \          /
                     +--------+
                          |
                          v
                 +------------------+
                 | Persistent State |
                 +--------+---------+
                          |
                          v
                      NEXT STEP
```

The language model is intentionally only one node inside the larger architecture.

---

# 6. Core Runtime

Quark Agent should have a small, stable core runtime.

The runtime should not know specifically how Obsidian, calendars, email, or web search work.

Those functions belong in skills.

The core runtime should provide universal agent infrastructure.

The core runtime consists of:

1. Capability Registry
2. Task Compiler
3. Task Intermediate Representation
4. Scheduler
5. Context Compiler
6. Model Runtime
7. Tool Executor
8. Validator
9. Recovery Engine
10. State Database
11. Permission System
12. Event Log

---

# 7. Capability Registry

Every Quark Agent skill registers the things it can do.

For example:

```text
obsidian.read_note
obsidian.list_notes
obsidian.search_notes
obsidian.generate_tags
obsidian.find_related_notes
obsidian.generate_frontmatter
obsidian.write_metadata
```

Future skills may register:

```text
calendar.list_events
calendar.find_event
calendar.find_free_time
calendar.create_event
calendar.update_event
```

or:

```text
web.search
web.fetch
web.extract
web.summarize
```

Each capability should have machine-readable metadata.

Example:

```yaml
name: obsidian.find_related_notes

description: >
  Finds notes that are semantically related to a target note.

domain: obsidian

operation_type: semantic

risk: read_only

inputs:
  note_id:
    type: string

  max_results:
    type: integer
    default: 5

outputs:
  related_notes:
    type: list

requires:
  - obsidian.read_note
  - obsidian.search_notes

model_required: true
```

The registry allows Quark Agent to discover capabilities dynamically.

---

# 8. Fixed Capabilities, Dynamic Composition

Quark Agent should follow this principle:

> **Capabilities are explicitly defined, but workflows may be dynamically composed.**

The model should not invent arbitrary capabilities.

For example, these operations may exist:

```text
calendar.find_event
calendar.list_events
obsidian.search_notes
obsidian.read_note
text.summarize
```

A user may then ask:

> Prepare me for my meeting tomorrow.

There may be no explicit `prepare_for_meeting` tool.

Quark Agent can compose:

```text
calendar.list_events
        |
        v
calendar.find_event
        |
        v
extract attendee/topic
        |
        v
obsidian.search_notes
        |
        v
obsidian.read_note
        |
        v
summarize relevant material
```

This creates emergent functionality without allowing the model to execute arbitrary uncontrolled operations.

---

# 9. Quark Intermediate Representation

Quark Agent should use a structured internal task representation.

This can be called **Quark IR**.

Instead of storing a task as natural language such as:

```text
Organize this note.
```

the compiler creates something like:

```yaml
goal:
  type: obsidian.organize_note
  note: meeting-sanctions.md

steps:

  - id: read_note
    op: obsidian.read_note

  - id: generate_tags
    op: obsidian.generate_tags
    depends_on:
      - read_note

  - id: find_links
    op: obsidian.find_related_notes
    depends_on:
      - read_note

  - id: generate_metadata
    op: obsidian.generate_frontmatter
    depends_on:
      - read_note
      - generate_tags

  - id: validate
    op: obsidian.validate_note
    depends_on:
      - generate_tags
      - find_links
      - generate_metadata

  - id: write
    op: obsidian.apply_changes
    depends_on:
      - validate
```

Quark IR acts like bytecode for the agent.

Natural-language requests are compiled into a structure the runtime can execute deterministically.

---

# 10. DAG-Based Execution

Quark Agent should model tasks as a directed acyclic graph rather than simply as a conversation or linear checklist.

Example:

```text
                     READ NOTE
                         |
            +------------+------------+
            |                         |
            v                         v
      GENERATE TAGS             EXTRACT TOPIC
            |                         |
            |                         v
            |                   SEARCH NOTES
            |                         |
            |                         v
            |                   RANK CANDIDATES
            |                         |
            |                         v
            |                   SELECT LINKS
            |                         |
            +------------+------------+
                         |
                         v
                GENERATE FRONTMATTER
                         |
                         v
                      VERIFY
                         |
                         v
                       WRITE
```

DAG-based execution provides several benefits.

Independent work can happen in parallel.

Failures can be retried locally.

Completed steps do not need to be repeated.

Dependencies are explicit.

The system knows exactly which data each operation requires.

This greatly reduces context requirements.

---

# 11. Context Compiler

The Context Compiler is one of the most important parts of Quark Agent.

Small language models perform much better when they receive only information relevant to the immediate decision.

The Context Compiler should therefore build a new minimal prompt for each semantic operation.

Suppose the full note is 1,500 tokens.

The model does not necessarily need all 1,500 tokens to choose between three project labels.

Instead Quark Agent might produce:

```text
TASK:
Determine which project this note belongs to.

AVAILABLE PROJECTS:
1. sanctions-paper
2. ceo-dataset
3. fasb-project
4. none

NOTE SUMMARY:
Advisor requested regressions separating critical and
non-critical inputs and analysis of hub-country imports.

OUTPUT:
Return exactly one project identifier.
```

This might consume fewer than 100 tokens.

The Context Compiler should be responsible for:

- selecting relevant facts,
- retrieving only relevant notes,
- selecting only relevant tools,
- shortening excessive context,
- inserting allowed values,
- defining output schemas,
- excluding irrelevant conversation history.

Quark Agent should avoid maintaining a giant traditional chat context.

---

# 12. Semantic Microtasks

Quark Agent should decompose semantic reasoning into narrowly scoped operations.

Core semantic operations might include:

```text
CLASSIFY
EXTRACT
COMPARE
SELECT
SUMMARIZE
TAG
RELATE
INTERPRET
RANK
VERIFY_SEMANTIC
```

Example:

```yaml
operation: CLASSIFY

input:
  note_summary: "..."

choices:
  - research
  - personal
  - admin
  - reference

output_schema:
  choice: enum
```

Model output:

```json
{
  "choice": "research"
}
```

This is much more reliable than asking:

> Analyze this note and decide what should be done with it.

---

# 13. Deterministic Execution Lane

Quark Agent should classify work into different execution lanes.

## Lane 0 — Deterministic

No language model required.

Examples:

```text
read file
write file
parse YAML
validate JSON
search filesystem
query SQLite
check file existence
calculate hashes
detect duplicates
verify links
parse dates
```

## Lane 1 — Small Model

Simple semantic tasks.

Examples:

```text
generate tags
classify note
extract action items
select project
compare short notes
rank related candidates
summarize text
```

## Lane 2 — Enhanced Small-Model Reasoning

More difficult semantic decisions.

Quark Agent may:

- provide additional context,
- split the question further,
- perform multiple attempts,
- vote between responses,
- ask a verifier model,
- retrieve additional information.

## Lane 3 — Escalation

If the local model cannot reliably complete the task:

- use a larger local model if available,
- request user input,
- mark the task unresolved.

Cloud access should be optional rather than architecturally required.

Quark Agent should remain fully functional as a local-only system.

---

# 14. Validation-First Architecture

No model output should automatically become trusted state.

Every semantic result should pass through a validator.

Example:

Model output:

```json
{
  "tags": [
    "sanctions",
    "regressions",
    "research"
  ]
}
```

Validator checks:

```text
Is JSON valid?
Is "tags" present?
Is tags a list?
Are tag strings valid?
Are there too many tags?
Are duplicates present?
Are forbidden characters present?
Do equivalent canonical tags already exist?
```

Only after validation should the result become part of the workflow state.

---

# 15. Semantic Verification

Some outputs cannot be checked deterministically.

For example:

> Is [[Hub Country Imports]] actually related to this note?

This may require a second semantic judgment.

Quark Agent may perform:

```text
Generator model:
"This note is related."

Verifier model:
"Are these two notes substantially related?
yes/no"
```

The generator and verifier may be the exact same underlying model running different prompts.

Quark Agent does not require multiple physical models.

It requires multiple logical roles.

---

# 16. Retry and Recovery System

Small models will frequently produce imperfect outputs.

Quark Agent should assume failure is normal.

A semantic step should therefore have a recovery strategy.

Example:

```text
attempt 1
    |
invalid JSON
    |
retry with explicit schema
    |
attempt 2
    |
invalid enum
    |
show valid choices
    |
attempt 3
```

Another example:

```text
related-note selection
        |
confidence too low
        |
retrieve more candidates
        |
compare again
```

The agent should recover from local errors rather than throwing away the entire workflow.

---

# 17. Confidence and Uncertainty

Model confidence should never be treated as a perfect probability, but it can be used as an additional routing signal.

Example:

```json
{
  "project": "sanctions-paper",
  "confidence": 0.94
}
```

Possible behavior:

```text
> 0.90
accept if deterministic validation succeeds

0.70–0.90
perform verifier pass

< 0.70
retrieve additional context

persistent uncertainty
ask user
```

Confidence can eventually be calibrated empirically against actual model accuracy.

---

# 18. State Management

Quark Agent should not rely on conversation history as memory.

Persistent state should live in a structured database.

SQLite is an ideal starting point.

Possible tables:

```text
goals
tasks
steps
step_dependencies
step_results
facts
capabilities
events
errors
permissions
model_calls
```

Example goal:

```json
{
  "goal_id": 1032,
  "goal_type": "obsidian.organize_note",
  "target": "meeting-sanctions.md",
  "status": "running"
}
```

Example steps:

```text
read_note             complete
generate_tags         complete
find_related_notes    running
generate_frontmatter  pending
validate_note         pending
write_note            pending
```

Because state is persistent, Quark Agent can survive:

- Pi reboots,
- model crashes,
- process failures,
- network outages,
- application restarts.

Execution can resume where it stopped.

---

# 19. Audit Log

Every meaningful action should be recorded.

For example:

```text
22:14:01 goal 1032 created
22:14:01 read meeting-sanctions.md
22:14:02 extracted note content
22:14:03 model tagger returned 5 tags
22:14:03 tags validated
22:14:04 searched 327 notes
22:14:05 retrieved 7 candidates
22:14:07 selected 3 related notes
22:14:07 links validated
22:14:08 generated frontmatter
22:14:08 YAML validated
22:14:09 note updated
22:14:09 final file validation passed
22:14:09 goal complete
```

This makes Quark Agent understandable and debuggable.

Users should be able to answer:

> Why did Quark change this file?

and receive an exact explanation.

---

# 20. Obsidian Skill Architecture

The first major skill should be packaged independently.

Possible structure:

```text
skills/
└── obsidian/
    ├── manifest.yaml
    ├── operations/
    │   ├── list_notes.py
    │   ├── read_note.py
    │   ├── search_notes.py
    │   ├── generate_tags.py
    │   ├── find_related_notes.py
    │   ├── generate_frontmatter.py
    │   ├── validate_note.py
    │   └── apply_changes.py
    │
    ├── prompts/
    │   ├── tagger.txt
    │   ├── note_classifier.txt
    │   ├── relationship_checker.txt
    │   └── metadata_extractor.txt
    │
    ├── schemas/
    │   ├── tags.json
    │   ├── relationships.json
    │   └── frontmatter.json
    │
    └── tests/
```

The Quark core should not care what an Obsidian note is.

The Obsidian skill implements that domain.

---

# 21. Obsidian Processing Pipeline

For each note:

```text
DISCOVER NOTE
     |
     v
READ FILE
     |
     v
PARSE EXISTING FRONTMATTER
     |
     v
NORMALIZE CONTENT
     |
     +------------------------+
     |                        |
     v                        v
GENERATE TAGS           EXTRACT TOPIC
     |                        |
     |                        v
     |                 RETRIEVE CANDIDATES
     |                        |
     |                        v
     |                  FIND RELATIONSHIPS
     |                        |
     +------------+-----------+
                  |
                  v
          GENERATE METADATA
                  |
                  v
             BUILD PATCH
                  |
                  v
              VALIDATE
                  |
                  v
             APPLY PATCH
                  |
                  v
           VERIFY RESULT
```

The language model should not perform the whole pipeline in one request.

---

# 22. Tag Taxonomy

Quark Agent should maintain a persistent tag index.

For example:

```text
sanctions
business-groups
research
regression
meeting
advisor
fasb
ceo-dataset
fintech
```

Before generating tags, Quark should retrieve likely tags.

Instead of asking:

> Invent tags for this note.

ask:

> Select appropriate tags from these 15 existing candidates. If none describe an important concept, propose at most one new tag.

This reduces taxonomy fragmentation.

Over time Quark can learn:

```text
sanctions-paper -> canonical project
sanction-paper -> alias
economic-sanctions -> related concept
```

---

# 23. Related Note Retrieval

Finding links should be a two-stage process.

## Stage A — Candidate Retrieval

Use inexpensive methods.

Potential methods include:

- note titles,
- tags,
- keyword overlap,
- BM25,
- SQLite FTS5,
- embeddings,
- project identifiers,
- people names.

Return perhaps:

```text
10 candidate notes
```

## Stage B — Semantic Ranking

The small model evaluates candidates.

For example:

```text
TARGET:
Advisor meeting about critical and non-critical
sanctioned inputs.

CANDIDATE:
Hub Country Import Analysis

Question:
Are these notes meaningfully related?

Return:
yes/no
relationship_type
confidence
```

The model never compares against the entire vault.

---

# 24. YAML Schema

Quark Agent should allow users to configure a vault schema.

Possible default:

```yaml
---
title:
type:
project:
created:
updated:
tags:
status:
action_required:
people:
---
```

Different note types may use different schemas.

For example:

```text
meeting-note
research-note
idea
reference
project
journal
task-note
```

A `meeting-note` might require:

```text
people
date
project
action_required
```

while a `reference` note might require:

```text
author
source
year
topic
```

The skill should support schema templates.

---

# 25. Safe File Modification

Quark Agent should never casually overwrite original notes.

Initial versions should use a safe write process:

```text
1. read original
2. calculate hash
3. create proposed modification
4. validate modification
5. check original has not changed
6. optionally create backup
7. atomically write new file
8. reopen file
9. parse result
10. verify expected changes
```

For early development, Quark should optionally run in:

```text
dry-run mode
```

where it shows intended changes without applying them.

---

# 26. Skill System

Every skill should have a manifest.

Example:

```yaml
skill:
  name: obsidian
  version: 0.1.0

capabilities:
  - obsidian.read_note
  - obsidian.search_notes
  - obsidian.generate_tags
  - obsidian.find_related_notes
  - obsidian.generate_frontmatter
  - obsidian.write_note

permissions:
  filesystem:
    read:
      - configured_vault

    write:
      - configured_vault
```

Future installation could look conceptually like:

```text
quark skill install calendar
quark skill install web-search
quark skill install email
```

The registry loads these skills automatically.

---

# 27. Permissions and Risk Classes

Every operation should include a risk class.

Possible classes:

```text
PURE
READ
WRITE
DESTRUCTIVE
EXTERNAL
PRIVILEGED
```

Examples:

```text
text.classify                  PURE
obsidian.read_note             READ
obsidian.write_note            WRITE
filesystem.delete              DESTRUCTIVE
email.send                     EXTERNAL
system.install_package         PRIVILEGED
```

Quark can then establish rules.

For example:

```text
PURE
automatic

READ
automatic

WRITE
automatic after validation

DESTRUCTIVE
require approval

EXTERNAL
require approval initially

PRIVILEGED
require approval
```

These policies should be configurable.

---

# 28. Model Interface

Quark should not depend tightly on Ollama.

Instead define a generic model provider interface.

For example:

```python
generate(
    prompt,
    schema=None,
    temperature=None,
    max_tokens=None
)
```

Providers may include:

```text
Ollama
llama.cpp
MLX
OpenAI-compatible endpoint
remote server
```

The Pi version can use Ollama initially.

The architecture remains portable.

---

# 29. Specialized Prompt Roles

Quark Agent should not have one gigantic system prompt.

Instead use small specialized prompt templates.

Examples:

```text
tagger
classifier
metadata_extractor
relationship_judge
summarizer
task_parser
verifier
repairer
```

All may use the same underlying model.

For example:

```text
Qwen 1.7B

tagger()
classifier()
relationship_judge()
verifier()
```

These are logical roles rather than separate agents.

---

# 30. Model Configuration

Model settings may differ by microtask.

For classification:

```text
temperature: low
output: constrained
max tokens: very low
```

For tag generation:

```text
temperature: moderate-low
```

For summarization:

```text
temperature: moderate
```

Each operation should be capable of defining its model policy.

---

# 31. Capability Retrieval

When Quark eventually has many skills, the model should not receive every available operation.

Suppose Quark contains 150 capabilities.

User says:

> Find notes related to my advisor meeting.

The runtime should identify that the task concerns:

```text
notes
meeting
search
```

and retrieve only relevant operations:

```text
obsidian.search_notes
obsidian.find_related_notes
obsidian.read_note
```

This reduces context size and tool-selection errors.

This becomes a form of **capability RAG**.

---

# 32. Reusable Procedures

Quark Agent should eventually support procedures composed from atomic capabilities.

For example:

```yaml
procedure: prepare_for_meeting

steps:
  - calendar.find_event
  - extract.attendees
  - obsidian.search_notes
  - obsidian.retrieve_recent
  - extract.action_items
  - summarize
```

Procedures provide reliable decomposition templates.

This avoids requiring a tiny model to independently invent every workflow.

Over time Quark can accumulate a library of reusable patterns.

---

# 33. Task Compiler

The Task Compiler converts user goals into Quark IR.

Initially, it should rely heavily on known task templates.

For example:

```text
"organize this note"
```

maps to:

```text
obsidian.organize_note
```

which already has a known workflow.

As Quark becomes more capable, the compiler may compose existing operations dynamically.

The important rule is:

> Avoid requiring the small model to design large workflows from scratch whenever a known decomposition already exists.

---

# 34. Why Templates Matter

The hardest part of agentic reasoning is frequently decomposition.

A weak model may be able to perform each step correctly while still creating a terrible plan.

Quark solves this partly by encoding procedural knowledge into the harness.

For example:

```text
ORGANIZE_NOTE
```

always expands into something approximately like:

```text
READ
PARSE
CLASSIFY
TAG
RETRIEVE
RELATE
GENERATE_METADATA
VALIDATE
WRITE
VERIFY
```

The model makes semantic judgments within this structure.

It does not have to invent the structure itself.

---

# 35. Scheduler

The scheduler determines which steps are ready.

Conceptually:

```python
while not goal.complete:

    ready_steps = graph.get_ready_steps()

    for step in ready_steps:

        result = executor.run(step)

        validation = validator.check(step, result)

        if validation.ok:
            state.commit(step, result)
        else:
            recovery.handle(step, result)
```

The scheduler should understand:

- dependencies,
- retries,
- timeouts,
- priorities,
- concurrency,
- failures,
- blocked tasks.

---

# 36. Event-Driven Architecture

Quark should eventually support events.

For example:

```text
NOTE_CREATED
NOTE_MODIFIED
CALENDAR_EVENT_CREATED
EMAIL_RECEIVED
FILE_DOWNLOADED
TIMER_TRIGGERED
```

The Obsidian MVP could support:

```text
note added to Inbox/
        |
        v
trigger Quark
        |
        v
classify note
        |
        v
tag
        |
        v
link
        |
        v
generate YAML
```

This allows Quark to become an ambient personal assistant rather than requiring every action to begin with a chat request.

---

# 37. Raspberry Pi Constraints

Quark Agent should treat low-resource hardware as a fundamental design constraint rather than an afterthought.

Optimization priorities should include:

- small context windows,
- short model generations,
- keeping models resident in memory,
- minimal repeated token generation,
- avoiding multiple simultaneous model instances,
- deterministic preprocessing,
- SQLite instead of heavyweight infrastructure,
- low-memory retrieval,
- caching,
- batching where appropriate.

Most model operations should ideally resemble:

```text
100–500 input tokens
5–100 output tokens
```

rather than multi-thousand-token agent conversations.

---

# 38. Caching

Because Quark may repeatedly analyze the same information, semantic results should be cached.

For example:

```text
hash(note_content) -> note_summary
hash(note_content) -> extracted_topics
hash(note_content) -> embedding
```

If the note has not changed, these computations should not be repeated.

Caching is particularly important on Raspberry Pi hardware.

---

# 39. Local Retrieval Index

The Obsidian skill should maintain an index.

Possible components:

```text
SQLite metadata database
SQLite FTS5 text index
optional vector embeddings
tag index
link graph
```

The first MVP may not even require embeddings.

A strong baseline could use:

```text
title matching
+ tags
+ FTS5
+ lexical similarity
```

and use the language model only for reranking.

Embeddings can be added later if they materially improve retrieval.

---

# 40. Suggested Project Structure

```text
quark-agent/
│
├── quark/
│   ├── core/
│   │   ├── runtime.py
│   │   ├── scheduler.py
│   │   ├── compiler.py
│   │   ├── context.py
│   │   ├── validator.py
│   │   ├── recovery.py
│   │   ├── permissions.py
│   │   └── events.py
│   │
│   ├── state/
│   │   ├── database.py
│   │   ├── models.py
│   │   └── migrations/
│   │
│   ├── models/
│   │   ├── provider.py
│   │   ├── ollama.py
│   │   └── prompts.py
│   │
│   ├── capabilities/
│   │   ├── registry.py
│   │   ├── loader.py
│   │   └── schemas.py
│   │
│   └── cli/
│
├── skills/
│   ├── obsidian/
│   └── core_text/
│
├── procedures/
│   └── organize_note.yaml
│
├── prompts/
│
├── tests/
│
├── benchmarks/
│
├── config/
│   └── quark.yaml
│
└── README.md
```

---

# 41. MVP Scope

The first version should remain deliberately narrow.

Recommended MVP operations:

## Filesystem

```text
filesystem.list_files
filesystem.read_file
filesystem.write_file
filesystem.file_exists
```

## Obsidian

```text
obsidian.list_notes
obsidian.read_note
obsidian.parse_frontmatter
obsidian.search_notes
obsidian.generate_tags
obsidian.find_related_notes
obsidian.generate_frontmatter
obsidian.validate_note
obsidian.apply_changes
```

## Generic Semantic

```text
text.classify
text.extract
text.compare
text.summarize
```

This is enough to test the core thesis.

---

# 42. MVP User Experience

The first interface can simply be a CLI.

Examples:

```text
quark organize ~/Obsidian/Inbox/note.md
```

or:

```text
quark organize-inbox
```

Possible output:

```text
Processing 7 notes...

[1/7] Meeting with Advisor.md

Project:
sanctions-paper

Tags added:
sanctions
research
regressions
business-groups

Related notes:
[[Sanctions Paper Roadmap]]
[[Hub Country Imports]]
[[Critical Inputs]]

YAML:
updated

Status:
success
```

A dry-run mode:

```text
quark organize-inbox --dry-run
```

should be available from the beginning.

---

# 43. Benchmarking

Quark Agent should be evaluated scientifically rather than subjectively.

Create a benchmark of real Obsidian notes.

For each note, create human-validated expectations for:

- correct project,
- acceptable tags,
- relevant related notes,
- correct metadata,
- malformed YAML rate,
- unnecessary changes.

Measure:

```text
tag precision
tag recall
project classification accuracy
related-note precision
related-note recall
YAML validity
end-to-end task completion
average retries
model calls per note
input tokens
output tokens
processing time
peak RAM
```

---

# 44. Architectural Benchmark

The central Quark hypothesis should be tested explicitly.

Compare:

```text
Small model + conventional agent
```

against:

```text
Same small model + Quark
```

Then compare:

```text
1B Quark
2B Quark
4B Quark
```

This reveals how much capability comes from architecture rather than raw model scale.

A particularly meaningful result would be:

```text
1–2B Quark
>
4B conventional ReAct agent
```

on structured multi-step workflows.

---

# 45. Error Benchmark

Quark should also be intentionally tested under failure.

Examples:

```text
malformed note
missing YAML delimiter
broken link
model emits invalid JSON
model hallucinates nonexistent note
file changes during processing
model server temporarily unavailable
duplicate tags
empty note
extremely large note
ambiguous project classification
```

The desired behavior is not that failure never occurs.

The desired behavior is:

> Fail locally, recover predictably, and never silently corrupt state.

---

# 46. Development Phases

## Phase 0 — Prototype

Build:

```text
read note
generate tags
generate YAML
write file
```

Use one local Ollama model.

Goal:

Prove basic local inference and safe file editing.

---

## Phase 1 — Quark Runtime

Add:

```text
SQLite state
structured operations
validation
retry system
event logging
```

Goal:

Separate the harness from the model.

---

## Phase 2 — Related Notes

Add:

```text
vault indexing
FTS retrieval
candidate ranking
semantic relationship checking
link generation
```

Goal:

Demonstrate retrieval + micro-reasoning.

---

## Phase 3 — Capability System

Move Obsidian functionality into an installable skill architecture.

Build:

```text
capability registry
skill manifests
permission declarations
dynamic loader
```

Goal:

Prove that Quark is a framework rather than an Obsidian script.

---

## Phase 4 — Second Skill

Calendar would be an excellent second major skill.

Capabilities:

```text
calendar.list_events
calendar.search
calendar.find_free_time
calendar.create_event
calendar.update_event
```

Goal:

Demonstrate that the architecture generalizes beyond notes.

---

## Phase 5 — Cross-Skill Composition

Example:

> Prepare me for tomorrow's research meeting.

Quark performs:

```text
calendar.search
→ identify meeting
→ identify project
→ obsidian.search
→ retrieve notes
→ extract outstanding tasks
→ summarize
```

Goal:

Demonstrate emergent workflows from independently developed skills.

---

## Phase 6 — External Information

Add web search.

Example:

> Update this research note with any major developments since I wrote it.

Quark uses:

```text
obsidian.read_note
web.search
web.fetch
text.compare
obsidian.update_note
```

Goal:

Extend Quark beyond local state.

---

# 47. What Quark Agent Is Not

Quark should deliberately avoid becoming another large general-purpose autonomous-agent framework.

It is not:

- a chatbot with 100 tools,
- a collection of agents talking to each other,
- a system based on huge chains of thought,
- a framework that assumes frontier models,
- an autonomous arbitrary-code generator,
- a replacement for deterministic software,
- a giant context window wrapped around an LLM.

Its differentiation comes from intentionally **minimizing the intelligence demanded from the model**.

---

# 48. The Quark Design Principles

The project should preserve the following principles as it grows.

### 1. Harness-first intelligence

Agentic competence should come from architecture whenever possible.

### 2. Minimal semantic workload

Ask the model the smallest meaningful question.

### 3. Determinism first

If software can solve it reliably, software should solve it.

### 4. Structured outputs

Prefer enums, JSON, schemas, and constrained choices to free-form text.

### 5. Validate everything

Probabilistic output does not become trusted state without validation.

### 6. Explicit state

Memory belongs in structured storage, not hidden conversation history.

### 7. Fixed primitives, dynamic composition

Capabilities are well-defined; workflows can be flexible.

### 8. Retrieval before reasoning

Reduce large search spaces before asking the model to judge.

### 9. Local failure

A failed step should not destroy an entire task.

### 10. Graceful escalation

Uncertainty should produce retries, additional context, or user intervention rather than hallucination.

### 11. Modular skills

New domains should be addable without redesigning the core runtime.

### 12. Local-first operation

Cloud services may eventually be supported, but Quark should remain useful without them.

---

# 49. The Core Research Question

Quark Agent is ultimately built around a broader question:

> **How much model intelligence can be replaced by good agent architecture?**

Modern AI development often treats increasing model capability as the primary solution to difficult agent tasks.

Quark explores the opposite direction.

Instead of asking:

> How large must the model become before it can reliably operate an agent?

Quark asks:

> How simple can we make each decision before a small model becomes sufficient?

This makes Quark both a useful personal software project and a potentially interesting experimental platform.

---

# 50. Long-Term Architecture

The mature system might look like:

```text
                           USER
                            |
                            v
                    NATURAL LANGUAGE
                            |
                            v
                    +---------------+
                    | Goal Parser   |
                    +-------+-------+
                            |
                            v
                    +---------------+
                    | Skill Search  |
                    +-------+-------+
                            |
                            v
                    +---------------+
                    | Task Compiler |
                    +-------+-------+
                            |
                            v
                    +---------------+
                    |   Quark IR    |
                    +-------+-------+
                            |
                            v
                  +---------------------+
                  | Workflow Scheduler  |
                  +----------+----------+
                             |
          +------------------+------------------+
          |                  |                  |
          v                  v                  v
     Deterministic      Semantic Model     Skill Runtime
        Engine             Runtime
          |                  |                  |
          +------------------+------------------+
                             |
                             v
                    +----------------+
                    | Validation     |
                    +--------+-------+
                             |
                      +------+------+
                      |             |
                    PASS           FAIL
                      |             |
                      |        +----v----+
                      |        | Recovery|
                      |        +----+----+
                      |             |
                      +------+------+
                             |
                             v
                      +-------------+
                      | State Store |
                      +------+------+
                             |
                             v
                       Goal Complete?
                         /       \
                       no         yes
                       |           |
                       +---->    RESULT
```

Skills surround this core:

```text
+---------------------------------------------------------+
|                       QUARK SKILLS                      |
|                                                         |
| Obsidian | Calendar | Email | Files | Web | Home | ... |
+---------------------------------------------------------+
```

The runtime remains unchanged as capabilities expand.

---

# 51. Quark Agent Identity

The name **Quark Agent** fits the architecture especially well.

A quark is a tiny fundamental component that combines with other components to create something much larger and more complex.

That mirrors the project's central philosophy:

```text
Tiny model decisions
+
Tiny deterministic operations
+
Tiny validated state transitions
+
Composition
=
Complex intelligent behavior
```

The intelligence of Quark should emerge not from any single massive reasoning operation, but from the careful composition of many small, reliable operations.

---

# 52. Initial Definition of Success

Quark Agent v0.1 should be considered successful if a Raspberry Pi can monitor or process an Obsidian inbox and, with minimal human intervention:

1. read each new note,
2. understand its basic topic,
3. assign useful existing tags,
4. propose new tags only when warranted,
5. identify genuinely related existing notes,
6. create valid Obsidian links,
7. generate consistent YAML frontmatter,
8. preserve existing note content,
9. avoid malformed files,
10. recover from model formatting errors,
11. maintain a complete record of its decisions,
12. run entirely using a small local model.

The MVP does not need to solve arbitrary tasks.

It needs to demonstrate one thing convincingly:

> **A carefully designed agent harness can make a very small local language model reliable enough to perform a meaningful multi-step workflow.**

If that premise succeeds with Obsidian, Quark Agent can then expand outward into calendars, email, search, files, research workflows, home automation, and other personal computing tasks.

That is the foundation of Quark Agent.