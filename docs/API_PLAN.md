# Kế hoạch API

Tài liệu này lập kế hoạch cho HTTP API tương lai của OpenMontage. Đây chỉ là
tài liệu kế hoạch: chưa thêm triển khai, tiến trình server, hoặc dependency
runtime mới.

## Mục tiêu

- Mở các workflow sản xuất video của OpenMontage cho client bên ngoài.
- Giữ đúng kiến trúc agent-first trong `AGENT_GUIDE.md`.
- Giữ code API Python là lớp biên mỏng cho validate, lưu trạng thái, job, và
  truy cập artifact.
- Không bypass pipeline manifest, stage director skill, tool discovery,
  checkpoint, và approval gate.
- Hỗ trợ job video chạy lâu bằng async execution và progress reporting.

## Không nằm trong phạm vi

- Không biến script demo như `make_dalat_travel_video.py` thành API surface
  chính thức.
- Không hardcode lựa chọn provider, tên API key, hoặc fallback path.
- Không tạo Python orchestrator thay thế contract agent/pipeline.
- Không chạy paid generation từ endpoint plan.
- Không tự đổi pipeline, provider, model, hoặc render runtime sau khi user đã
  duyệt.

## Kiến trúc

```text
Client / Web UI / MCP
        |
        v
HTTP API layer
        |
        v
Service layer
        |
        +--> tool registry discovery
        +--> pipeline manifest loading
        +--> checkpoint reads/writes
        +--> artifact reads/writes
        +--> background job scheduling
        |
        v
OpenMontage tools and render runtimes
```

API layer nên nhỏ. Nó nhận request, validate payload, tạo hoặc đọc project
workspace, schedule job, và expose artifact. Quyết định thực thi pipeline vẫn do
manifest, skill, checkpoint, và user approval chi phối.

## Cấu trúc thư mục đề xuất

```text
api/
├── main.py
├── schemas.py
├── services/
│   ├── capability_service.py
│   ├── project_service.py
│   ├── pipeline_service.py
│   └── job_service.py
└── workers/
    └── runner.py
```

Cấu trúc này chỉ là đề xuất. Phần triển khai nên được thêm trong PR riêng, phạm
vi rõ ràng, sau khi kế hoạch này được chấp nhận.

## Tài nguyên lõi

### Project

Project map với quy ước workspace hiện có:

```text
projects/<project-name>/
├── artifacts/
├── assets/
└── renders/
```

API không bao giờ ghi production asset vào repository root.

### Pipeline

Pipeline map với manifest trong `pipeline_defs/`. API nên liệt kê pipeline bằng
cách đọc manifest, không giữ danh sách hardcoded riêng.

### Artifact

Artifact là contract JSON chuẩn giữa các stage, ví dụ:

- `brief`
- `script`
- `scene_plan`
- `asset_manifest`
- `edit_decisions`
- `render_report`
- `publish_log`

Artifact nên được validate theo schema trong `schemas/artifacts/` khi tạo hoặc
cập nhật.

### Job

Job đại diện cho tác vụ chạy lâu như stage execution hoặc render. Job phải chạy
async vì video generation, TTS, stock search, và rendering có thể mất nhiều phút.

## Kế hoạch endpoint

### Health

```http
GET /health
```

Trả trạng thái cơ bản của service.

Ví dụ response:

```json
{
  "status": "ok",
  "service": "openmontage-api"
}
```

### Capabilities

```http
GET /capabilities
```

Trả summary thân thiện với user từ tool registry. Nên dựa trên
`registry.provider_menu_summary()` và chỉ thêm detail sâu khi được yêu cầu.

Ví dụ response:

```json
{
  "composition_runtimes": {
    "ffmpeg": true,
    "remotion": true,
    "hyperframes": false
  },
  "capabilities": [
    {
      "capability": "tts",
      "configured": 1,
      "total": 3,
      "providers": ["example-provider"]
    }
  ],
  "setup_offers": [],
  "runtime_warnings": []
}
```

### Pipelines

```http
GET /pipelines
```

Liệt kê pipeline manifest có trong `pipeline_defs/`.

Ví dụ response:

```json
{
  "pipelines": [
    {
      "id": "hybrid",
      "stability": "production",
      "description": "Source-plus-support hybrid video workflow"
    }
  ]
}
```

### Tạo project

```http
POST /projects
```

Tạo project workspace và metadata ban đầu.

Ví dụ request:

```json
{
  "title": "Da Lat Travel Documentary",
  "slug": "dalat-travel-documentary"
}
```

Ví dụ response:

```json
{
  "project_id": "dalat-travel-documentary",
  "path": "projects/dalat-travel-documentary",
  "status": "created"
}
```

### Lấy project

```http
GET /projects/{project_id}
```

Trả project metadata, checkpoint đã biết, artifact, và trạng thái render.

Ví dụ response:

```json
{
  "project_id": "dalat-travel-documentary",
  "status": "awaiting_approval",
  "current_stage": "idea",
  "checkpoints": [
    {
      "stage": "idea",
      "status": "awaiting_human"
    }
  ],
  "render": null
}
```

### Tạo production plan

```http
POST /projects/{project_id}/plan
```

Tạo hoặc cập nhật plan artifact. Endpoint này không được bắt đầu paid generation
hoặc asset generation có hệ quả lớn. Nó nên thực hiện capability discovery,
pipeline matching, cost estimation, và chuẩn bị approval gate.

Ví dụ request:

```json
{
  "brief": "Create a 60-second Vietnamese documentary montage about Da Lat.",
  "pipeline": "hybrid",
  "duration_seconds": 60,
  "language": "vi",
  "style": "clean-professional",
  "target_platform": "youtube"
}
```

Ví dụ response:

```json
{
  "project_id": "dalat-travel-documentary",
  "status": "awaiting_approval",
  "recommended_pipeline": "hybrid",
  "render_runtime_options": ["remotion", "hyperframes", "ffmpeg"],
  "recommended_render_runtime": "remotion",
  "artifact": "projects/dalat-travel-documentary/artifacts/proposal_packet.json"
}
```

### Duyệt stage hoặc plan

```http
POST /projects/{project_id}/approve
```

Ghi nhận user approval cho plan hoặc stage. Asset generation và rendering chỉ nên
chạy sau khi approval gate bắt buộc đã thỏa mãn.

Ví dụ request:

```json
{
  "stage": "idea",
  "approved": true,
  "notes": "Proceed with Remotion and Vietnamese narration."
}
```

Ví dụ response:

```json
{
  "project_id": "dalat-travel-documentary",
  "stage": "idea",
  "status": "approved"
}
```

### Chạy các stage pipeline

```http
POST /projects/{project_id}/run
```

Schedule stage execution thành background job.

Ví dụ request:

```json
{
  "from_stage": "script",
  "to_stage": "compose"
}
```

Ví dụ response:

```json
{
  "job_id": "job_01hxyz",
  "project_id": "dalat-travel-documentary",
  "status": "queued"
}
```

### Lấy artifact

```http
GET /projects/{project_id}/artifacts
GET /projects/{project_id}/artifacts/{artifact_name}
```

Liệt kê hoặc trả canonical artifact.

Ví dụ response:

```json
{
  "artifacts": [
    "brief",
    "script",
    "scene_plan",
    "edit_decisions",
    "render_report"
  ]
}
```

### Lấy render

```http
GET /projects/{project_id}/render
```

Trả metadata của final render khi có.

Ví dụ response:

```json
{
  "project_id": "dalat-travel-documentary",
  "status": "completed",
  "file": "projects/dalat-travel-documentary/renders/final.mp4",
  "render_report": "projects/dalat-travel-documentary/artifacts/render_report.json"
}
```

### Tải render

```http
GET /projects/{project_id}/render/download
```

Stream file video cuối sau khi xác minh file thuộc đúng project workspace được
yêu cầu.

### Trạng thái job

```http
GET /jobs/{job_id}
```

Trả trạng thái job hiện tại.

Ví dụ response:

```json
{
  "job_id": "job_01hxyz",
  "status": "running",
  "project_id": "dalat-travel-documentary",
  "stage": "assets",
  "progress": 0.42,
  "message": "Downloading stock footage"
}
```

### Job events

```http
GET /jobs/{job_id}/events
```

Server-Sent Events stream cho progress update. Khuyến nghị dùng cho web UI và
MCP-style long-running tool calls.

Ví dụ event:

```text
event: progress
data: {"stage":"compose","progress":0.8,"message":"Rendering final video"}
```

## Contract thực thi stage

API runner nên thực thi stage theo thứ tự này:

1. Load pipeline manifest đã chọn từ `pipeline_defs/`.
2. Discover capability qua tool registry.
3. Check required tools và fallback tools.
4. Đọc checkpoint state hiện tại.
5. Xác nhận có human approval bắt buộc trước paid work hoặc consequential work.
6. Chạy từng stage một.
7. Validate canonical artifact được tạo.
8. Ghi checkpoint.
9. Emit job progress.
10. Dừng tại approval gate tiếp theo khi bắt buộc.

Runner không được âm thầm skip stage lỗi, thay provider, hoặc downgrade render
runtime mà không trả blocker cho API client.

## Chọn render runtime

Khi có nhiều hơn một composition runtime, plan response phải expose mọi option có
ý nghĩa. Với mỗi option, client nên nhận:

- tên runtime
- lý do phù hợp với brief hiện tại
- tradeoff
- recommendation
- availability status

Sau khi được duyệt, `render_runtime` đã chọn phải được ghi trong plan hoặc
`edit_decisions` artifact và giữ nguyên qua stage compose.

## Error model

Dùng structured error để client biết nên hỏi user, retry, hoặc mở setup
instructions.

Ví dụ error response:

```json
{
  "error": {
    "code": "APPROVAL_REQUIRED",
    "message": "Stage assets requires approval before execution.",
    "project_id": "dalat-travel-documentary",
    "stage": "assets",
    "next_actions": [
      "POST /projects/dalat-travel-documentary/approve"
    ]
  }
}
```

Error code khuyến nghị:

- `VALIDATION_ERROR`
- `PROJECT_NOT_FOUND`
- `PIPELINE_NOT_FOUND`
- `ARTIFACT_NOT_FOUND`
- `APPROVAL_REQUIRED`
- `CAPABILITY_BLOCKED`
- `PROVIDER_UNAVAILABLE`
- `RUNTIME_UNAVAILABLE`
- `JOB_NOT_FOUND`
- `JOB_FAILED`
- `RENDER_NOT_FOUND`

## Bảo mật và an toàn

- Không expose raw `.env` values.
- Không trả provider API key hoặc secret trong capability response.
- Giới hạn file read và download trong project workspace được yêu cầu.
- Validate project slug để chặn path traversal.
- Xem uploaded media là untrusted input.
- Tách provider setup instructions khỏi secret values.
- Ghi lại paid action hoặc consequential action trong job log và checkpoint.

## Milestone triển khai ban đầu

### Milestone 1: Read-only API

- `GET /health`
- `GET /capabilities`
- `GET /pipelines`
- `GET /projects/{project_id}`
- `GET /projects/{project_id}/artifacts`

### Milestone 2: Project và Planning API

- `POST /projects`
- `POST /projects/{project_id}/plan`
- `POST /projects/{project_id}/approve`

### Milestone 3: Async Execution

- `POST /projects/{project_id}/run`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events`

### Milestone 4: Render Access

- `GET /projects/{project_id}/render`
- `GET /projects/{project_id}/render/download`

### Milestone 5: Production Hardening

- persistent job queue
- auth layer
- rate limiting
- structured audit logs
- artifact schema validation error tốt hơn
- integration test cho approval gate và runtime selection

## Câu hỏi mở

- Nên dùng HTTP framework nào trước: FastAPI, Flask, hay MCP-first server wrapper?
- Job nên bắt đầu bằng in-memory state cho local use hay Redis-backed state cho
  production use?
- API client có được request provider cụ thể không, hay chỉ được nêu capability
  preference?
- Endpoint plan nên gọi agent layer, hay chỉ chuẩn bị dữ liệu để external agent
  duyệt và thực thi?
- Cần authentication model nào cho local-only use và hosted use?
