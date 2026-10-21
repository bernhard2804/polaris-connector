# ADR 001: Agent-Runtime über Pydantic AI hinter dünnen Polaris-Ports

- Status: Accepted for isolated PoC
- Datum: 2026-09-20
- Entscheider: lokaler Polaris-/Omarchy-Stack
- Vollständige Evaluation: [../agent-runtime-evaluation.md](../agent-runtime-evaluation.md)

## Kontext

Polaris besitzt heute Source Intake, append-only Rohablage und einen Curator für
Langzeitwissen, aber keine allgemeine, crash-sichere Agent-Runtime. Eine neue
Runtime muss OpenAI, Anthropic, DeepSeek und Ollama austauschbar unterstützen,
Sessions und Memory trennen, MCP/Skills integrieren, Provider-Prompt-Caching
begünstigen und OpenTelemetry liefern.

Ein vollständiges eigenes Harness würde Agent Loop, Tool Calling, Resume,
Compaction und Telemetrie erneut implementieren. Das widerspricht der
Priorisierung vorhandener gepflegter Lösungen.

## Entscheidung

Wir wählen **B: vorhandenes Framework plus dünne eigene Abstraktionsschicht**.

Für den PoC:

- Pydantic AI liefert Agent Loop, Provideradapter, MCP, Tool Calling,
  Approval-Mechanismen, Usage und OpenTelemetry.
- Pydantic AI Harness liefert `StepPersistence` und nur die einzeln benötigten
  Capabilities.
- Versionen werden exakt gepinnt. Der Harness bleibt wegen 0.x-Status isoliert.
- Der PoC verwendet keine pauschale `Coder`-Capability und keinen Shellzugriff,
  sondern ausschließlich explizite read-only Tools.
- Native `StepPersistence` ist die Resume-Quelle.
- Jede Session ist in ein eigenes, versioniertes JSONL-Audit-/Exitformat
  exportierbar. Dieses Format ist nicht die Agent-Loop-Implementierung.

## Eigene Verantwortungsbereiche

1. Konfiguration: Task-Klasse, Profil und konkrete Modellroute.
2. Contracts: provider-neutrale Session-Summary, Policy-Klassen und Metrics.
3. Memory-Port: read-only Retrieval aus bestehendem Machine-/Projekt-Memory;
   Schreiben erfolgt ausschließlich über den Curator.
4. Policy-Adapter: Fähigkeiten und Approval-Anforderungen aus einer expliziten
   Policy ableiten.
5. Export: `polaris.session.v1` JSONL und Migrations-/Golden-Tests.

## Delegierte Verantwortungsbereiche

Agent Loop, Modellprotokolle, Streaming, Tool Dispatch, MCP, native Session-
Persistenz, Resume, Compaction-Mechanismen und OTel-Spans bleiben im Framework.

Wir bauen ausdrücklich keinen eigenen Loop, Checkpointer, MCP-Client,
KV-/Response-Cache, Modellrouter oder Multi-Agent-Orchestrator.

## Begründung

Pydantic AI passt zum vorhandenen Python-Code und unterstützt die geforderten
Provider einschließlich Ollama ohne einen Gateway-Zwang. Die Kombination hat
native OTel-GenAI-Instrumentierung, normalisierte Cache-Usage und eine
Step-Persistenz mit append-only Events, sicheren Snapshots und einem
Tool-Effect-Ledger. Damit deckt sie den PoC mit weniger eigener Infrastruktur
ab als Microsoft Agent Framework oder LangGraph/Deep Agents.

Microsoft Agent Framework bleibt die Rückfalloption, wenn API-Stabilität höher
gewichtet werden muss; sein `AgentSession` ist jedoch ein opaker Snapshot und
damit ein schwächeres Exit-/Auditfundament. DeepSeek Harness hat das beste
Event-Sourcing-Modell, ist aber ausdrücklich nicht produktionsreif. Deep Agents
ist für den aktuellen Einzelagenten-PoC unnötig umfangreich.

## Konsequenzen

Positiv:

- kleine Eigenfläche und klarer Ersatzpunkt;
- gleiche fachliche Agentenlogik für Cloud und lokal;
- Session-/Memory-Trennung bleibt erhalten;
- echte Provider-Cache-Metriken können gemessen werden;
- kein neuer dauerhafter Dienst für den PoC.

Negativ:

- Harness-0.x-Upgrades benötigen Migrationstests;
- StepPersistence ist noch kein vollständig branchbarer Event Store;
- Snapshots benötigen begrenzte Retention bei langen Runs;
- harte Host-Sandboxing-/Netzwerkgrenzen löst das Framework nicht allein.

## Sicherheitsentscheidung

Der PoC ist read-only und arbeitet nur in seinem Fixture-Workspace. Keine
Shell-, Netzwerk-, Secret- oder Destructive-Capability wird registriert. Spätere
Capabilities müssen Policy-Klassen `read-only`, `workspace-write`,
`shell-execute`, `network`, `secrets` und `destructive/system` explizit
deklarieren. Approval ersetzt keine OS-Sandbox und keine Authentifizierung.

## Exit-Kriterien für den PoC

- Start und Resume funktionieren in getrennten Prozessen.
- Mindestens ein Tool Call ist in nativer Persistenz und Export nachweisbar.
- Providerwechsel benötigt keine Änderung der fachlichen Agentenlogik.
- OTel enthält Run-, Model- und Tool-Spans.
- Kein produktiver Polaris-/Omarchy-Pfad wurde verändert.
- Offline-Tests sind deterministisch.

## Neubewertung

Die Entscheidung wird neu bewertet, wenn mindestens eines gilt:

- zwei aufeinanderfolgende notwendige Harness-Upgrades brechen unsere Ports;
- StepPersistence verliert Resume-/Auditgarantien;
- Microsoft Agent Framework erhält ein offenes append-only Session-Backend;
- DeepSeek Harness verlässt Developer Preview und besteht eine
  Security-/Betriebsprüfung;
- ein echter Graph-/Multi-Agent-Use-Case rechtfertigt LangGraph.
