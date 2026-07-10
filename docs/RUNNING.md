# OpenMontage — Hướng Dẫn Cài Đặt, Cấu Hình & Khởi Chạy

Tài liệu này hướng dẫn chi tiết từng bước để cài đặt, cấu hình và chạy dự án **OpenMontage** trên máy của bạn. OpenMontage là một nền tảng sản xuất video do AI điều phối (agentic video production), hoạt động thông qua trợ lý mã hóa AI như Claude Code, Cursor, Copilot, Windsurf hoặc Codex.

> **Mục tiêu:** Sau khi làm theo hướng dẫn này, bạn sẽ có một môi trường OpenMontage sẵn sàng chạy, có thể tạo video từ mô tả bằng ngôn ngữ tự nhiên.

---

## 📋 Mục Lục

1. [Yêu Cầu Hệ Thống](#yêu-cầu-hệ-thống)
2. [Tổng Quan Các Bước](#tổng-quan-các-bước)
3. [Bước 1: Cài Đặt Môi Trường Cơ Bản](#bước-1-cài-đặt-môi-trường-cơ-bản)
4. [Bước 2: Clone Repository](#bước-2-clone-repository)
5. [Bước 3: Cài Đặt Phụ Thuộc Python](#bước-3-cài-đặt-phụ-thuộc-python)
6. [Bước 4: Cài Đặt Remotion Composer](#bước-4-cài-đặt-remotion-composer)
7. [Bước 5: Cài Đặt TTS Ngoại Tuyến Piper (Tùy Chọn, Khuyến Nghị)](#bước-5-cài-đặt-tts-ngoại-tuyến-piper-tùy-chọn-khuyến-nghị)
8. [Bước 6: Chuẩn Bị HyperFrames Runtime](#bước-6-chuẩn-bị-hyperframes-runtime)
9. [Bước 7: Tạo File `.env` và Cấu Hình API Key](#bước-7-tạo-file-env-và-cấu-hình-api-key)
10. [Bước 8: Cấu Hình `config.yaml` (Tùy Chọn)](#bước-8-cấu-hình-configyaml-tùy-chọn)
11. [Bước 9: Kiểm Tra Preflight & Khả Năng Hệ Thống](#bước-9-kiểm-tra-preflight--khả-năng-hệ-thống)
12. [Bước 10: Chạy Demo Không Cần API Key](#bước-10-chạy-demo-không-cần-api-key)
13. [Bước 11: Bắt Đầu Sản Xuất Video](#bước-11-bắt-đầu-sản-xuất-video)
14. [Cài Đặt GPU (Tùy Chọn)](#cài-đặt-gpu-tùy-chọn)
15. [Xử Lý Sự Cố Thường Gặp](#xử-lý-sự-cố-thường-gặp)
16. [Lệnh Makefile Tham Khảo](#lệnh-makefile-tham-khảo)
17. [Tài Liệu Tham Khảo](#tài-liệu-tham-khảo)

---

## Yêu Cầu Hệ Thống

### Phần Mềm Bắt Buộc

| Phần mềm | Phiên bản tối thiểu | Mục đích | Link tải |
|---|---|---|---|
| **Python** | 3.10+ | Chạy các công cụ Python lõi | <https://www.python.org/downloads/> |
| **FFmpeg** | bản ổn định mới nhất | Mã hóa video, ghép nối, burn subtitle | <https://ffmpeg.org/download.html> |
| **Node.js** | 18+ (khuyến nghị 22+ cho HyperFrames) | Chạy Remotion và HyperFrames | <https://nodejs.org/> |
| **npm** | Đi kèm Node.js | Quản lý gói Node.js | <https://nodejs.org/> |
| **Git** | bản ổn định | Clone repository | <https://git-scm.com/> |

### Yêu Cầu Phần Cứng

- **Tối thiểu:** Máy tính hiện đại có thể chạy Python và Node.js, 8 GB RAM, 10 GB dung lượng ổ đĩa trống.
- **Khuyến nghị:** 16 GB RAM, SSD, card đồ họa NVIDIA nếu muốn tạo video bằng mô hình local (Wan, Hunyuan, CogVideo, LTX).
- **Mạng:** Kết nối internet để tải dependency, npx package và gọi API cloud (nếu có key).

### Môi Trường Được Hỗ Trợ

- ✅ Windows 10/11 (PowerShell hoặc Git Bash)
- ✅ macOS (Intel & Apple Silicon)
- ✅ Linux (Ubuntu/Debian/Fedora)

> **Ghi chú Windows:** Một số lệnh `make` không sẵn có sẵn. Bạn có thể dùng Git Bash, WSL2, hoặc cài `make` qua Chocolatey (`choco install make`). Tất cả lệnh bên dưới đều có phiên bản thay thế không cần `make`.

---

## Tổng Quan Các Bước

```text
1. Cài Python, FFmpeg, Node.js
2. git clone OpenMontage
3. pip install -r requirements.txt
4. cd remotion-composer && npm install
5. pip install piper-tts
6. npx --yes hyperframes --version
7. cp .env.example .env  → điền API key
8. Kiểm tra preflight
9. make demo  hoặc  python render_demo.py
10. Bắt đầu chat với AI assistant
```

---

## Bước 1: Cài Đặt Môi Trường Cơ Bản

### 1.1 Cài Python

Tải Python 3.10+ từ <https://www.python.org/downloads/>. Trong quá trình cài đặt trên Windows, **tích chọn "Add Python to PATH"**.

Kiểm tra sau khi cài:

```bash
python --version       # hoặc python3 --version trên macOS/Linux
python -m pip --version
```

Nếu chưa có, cũng khuyến nghị tạo virtual environment:

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 1.2 Cài FFmpeg

- **macOS:** `brew install ffmpeg`
- **Ubuntu/Debian:** `sudo apt update && sudo apt install ffmpeg`
- **Windows:** Tải từ <https://ffmpeg.org/download.html>, giải nén và thêm `bin/` vào PATH. Hoặc dùng Chocolatey: `choco install ffmpeg`.

Kiểm tra:

```bash
ffmpeg -version
```

### 1.3 Cài Node.js

Tải LTS từ <https://nodejs.org/>. Khuyến nghị Node.js 22+ để HyperFrames hoạt động ổn định.

Kiểm tra:

```bash
node --version
npm --version
npx --version
```

---

## Bước 2: Clone Repository

```bash
git clone https://github.com/calesthio/OpenMontage.git
cd OpenMontage
```

Sau khi clone, cấu trúc thư mục chính sẽ như sau:

```text
OpenMontage/
├── .env.example            # Mẫu biến môi trường
├── config.yaml             # Cấu hình toàn cục
├── requirements.txt        # Python dependency lõi
├── requirements-dev.txt    # Dependency phát triển
├── requirements-gpu.txt    # Dependency GPU (tùy chọn)
├── Makefile                # Lệnh tự động hóa
├── setup.py                # Cài đặt package openmontage
├── remotion-composer/      # React/Remotion composition engine
├── tools/                  # Các công cụ Python
├── skills/                 # Kỹ năng/knowledge cho agent
├── pipeline_defs/          # Định nghĩa pipeline YAML
├── lib/                    # Thư viện lõi
├── schemas/                # JSON schema kiểm tra
├── styles/                 # Style playbook
├── docs/                   # Tài liệu kỹ thuật
└── tests/                  # Kiểm thử
```

---

## Bước 3: Cài Đặt Phụ Thuộc Python

### Cách nhanh nhất: dùng `make`

```bash
make install
```

### Cách thủ công

```bash
python -m pip install -r requirements.txt
```

Nội dung `requirements.txt`:

```text
pyyaml>=6.0
pydantic>=2.0
jsonschema>=4.20
python-dotenv>=1.0
Pillow>=10.0
requests>=2.31
google-auth>=2.0       # service-account auth cho Google TTS + Imagen (Vertex AI)
```

Ngoài ra cũng khuyến nghị cài đặt package dự án:

```bash
python -m pip install -e .
```

Lệnh này cài `openmontage` package theo `setup.py`.

### Cài dependency phát triển (nếu muốn chạy test)

```bash
make install-dev
# hoặc
python -m pip install -r requirements-dev.txt
```

---

## Bước 4: Cài Đặt Remotion Composer

Remotion là công cụ composition dựa trên React, dùng để tạo video từ ảnh tĩnh, biểu đồ, text card, caption, v.v.

```bash
cd remotion-composer
npm install
cd ..
```

> **Lưu ý Windows:** Nếu `npm install` báo lỗi `ERR_INVALID_ARG_TYPE`, hãy chạy: `npx --yes npm install`

Sau khi cài xong, trong thư mục `remotion-composer/` sẽ có `node_modules/`.

---

## Bước 5: Cài Đặt TTS Ngoại Tuyến Piper (Tùy Chọn, Khuyến Nghị)

Piper là TTS miễn phí, chạy hoàn toàn ngoại tuyến, giúp bạn tạo video mà không cần API key.

```bash
python -m pip install piper-tts
```

Nếu cài thất bại (thường do hệ điều hành hoặc phiên bản Python không tương thích), bạn vẫn có thể dùng các nhà cung cấp TTS cloud (ElevenLabs, Google, OpenAI) bằng cách thêm API key vào `.env`.

---

## Bước 6: Chuẩn Bị HyperFrames Runtime

HyperFrames là engine composition thứ hai, dùng HTML/CSS/GSAP, rất mạnh cho kinetic typography, product promo, website-to-video và SVG character animation.

### Kiểm tra HyperFrames có thể resolve qua npx không:

```bash
npx --yes hyperframes --version
```

Nếu thành công, nó sẽ in ra phiên bản HyperFrames CLI. Lần chạy đầu tiên có thể mất 30–60 giây để tải package.

### Kiểm tra runtime đầy đủ:

```bash
make hyperframes-doctor
```

Hoặc chạy Python trực tiếp:

```bash
python -c "from tools.video.hyperframes_compose import HyperFramesCompose; import json; r=HyperFramesCompose().execute({'operation':'doctor'}); print(json.dumps(r.data, indent=2)); print('OK' if r.success else f'FAIL: {r.error}')"
```

Nếu báo lỗi, kiểm tra lại Node.js ≥ 22, FFmpeg trong PATH, và khả năng kết nối internet để npx tải package.

---

## Bước 7: Tạo File `.env` và Cấu Hình API Key

File `.env` chứa tất cả API key và cấu hình nhạy cảm. **Không đẩy file này lên Git** — nó đã có trong `.gitignore`.

### 7.1 Tạo `.env` từ mẫu

```bash
cp .env.example .env
```

Trên Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### 7.2 Cấu hình API key theo nhu cầu

Mở `.env` bằng trình soạn thảo văn bản. Dưới đây là danh sách các key phổ biến và mục đích:

#### Image + Video Gateway (Fal.ai)

```bash
FAL_KEY=your_fal_key_here
```

FAL_KEY mở khóa: FLUX image, Google Veo video, Kling video, MiniMax video, Recraft image.
Lấy key tại: <https://fal.ai/dashboard/keys>

#### Google

```bash
GOOGLE_API_KEY=your_google_key_here
```

Dùng cho Google Imagen image và Google Cloud TTS (700+ giọng, 50+ ngôn ngữ).
Lấy key tại: <https://aistudio.google.com/apikey>

Thay thế bằng service-account:

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

#### Voice / TTS

```bash
ELEVENLABS_API_KEY=your_key      # Premium TTS, nhạc, hiệu ứng âm thanh
OPENAI_API_KEY=your_key          # OpenAI TTS + DALL-E
XAI_API_KEY=your_key             # Grok image/video
DOUBAO_SPEECH_API_KEY=your_key   # Volcengine Doubao TTS
DOUBAO_SPEECH_VOICE_TYPE=zh_female_vv_uranus_bigtts
```

#### Music

```bash
SUNO_API_KEY=your_key            # Suno AI music generation
```

#### Video Generation

```bash
HEYGEN_API_KEY=your_key          # HeyGen gateway: VEO, Sora, Runway, Kling
RUNWAY_API_KEY=your_key          # Runway Gen-4 direct
VIDEO_GEN_LOCAL_ENABLED=true     # Bật video generation local (cần GPU)
VIDEO_GEN_LOCAL_MODEL=wan2.1-1.3b # hoặc wan2.1-14b, hunyuan-1.5, ltx2-local, cogvideo-5b
MODAL_LTX2_ENDPOINT_URL=https://your-modal-endpoint  # (tùy chọn)
```

#### Stock Media (Miễn Phí)

```bash
PEXELS_API_KEY=your_key          # Free stock footage/images
PIXABAY_API_KEY=your_key         # Free stock footage/images
UNSPLASH_ACCESS_KEY=your_key     # Free stock images
```

#### Analysis

```bash
HF_TOKEN=your_huggingface_token  # Cần cho speaker diarization trong transcriber
```

### 7.3 Lưu ý bảo mật

- Không bao giờ commit `.env`.
- Không chia sẻ API key trong chat công khai.
- Nếu làm việc nhóm, dùng `.env.example` để chia sẻ tên biến, không chia sẻ giá trị.

### 7.4 Có thể chạy mà không cần API key?

**Có.** OpenMontage hỗ trợ đường dẫn hoàn toàn miễn phí:

- TTS: Piper (local)
- Stock footage: Archive.org, NASA, Wikimedia Commons (không cần key)
- Composition: Remotion + FFmpeg (local)
- Subtitle: Built-in

Tuy nhiên, để có nhiều lựa chọn provider cloud, bạn nên thêm ít nhất 1–2 key (FAL_KEY hoặc GOOGLE_API_KEY hoặc OPENAI_API_KEY).

---

## Bước 8: Cấu Hình `config.yaml` (Tùy Chọn)

File `config.yaml` điều khiển hành vi toàn cục của hệ thống.

```yaml
llm:
  provider: anthropic            # anthropic | openai | gemini | openrouter | ollama | mistral | minimax
  model: null                    # null = dùng model mặc định của provider
  temperature: 0.7
  max_tokens: 4096

budget:
  mode: warn                     # observe | warn | cap
  total_usd: 10.00
  reserve_pct: 0.10
  single_action_approval_usd: 0.50
  require_approval_for_new_paid_tool: true

checkpoint:
  policy: guided                 # guided | manual_all | auto_noncreative
  storage_dir: pipeline

output:
  default_format: mp4
  default_codec: libx264
  default_audio_codec: aac
  default_resolution: "1920x1080"
  default_fps: 30
  default_crf: 23

paths:
  pipeline_dir: pipeline
  library_dir: library
  styles_dir: styles
  skills_dir: skills
  output_dir: output
```

### Giải thích các tùy chọn quan trọng

| Section | Key | Ý nghĩa |
|---|---|---|
| `llm` | `provider` | LLM mà agent dùng để điều phối pipeline. Cần API key tương ứng. |
| `budget` | `mode` | `observe` = chỉ theo dõi; `warn` = cảnh báo khi vượt; `cap` = chặn cứng. |
| `budget` | `total_usd` | Ngân sách tối đa cho một production run. |
| `budget` | `single_action_approval_usd` | Chi phí từng hành động vượt ngưỡng này sẽ yêu cầu phê duyệt. |
| `checkpoint` | `policy` | `guided` = agent tự quyết định checkpoint; `manual_all` = luôn dừng chờ; `auto_noncreative` = tự động qua các stage kỹ thuật. |
| `output` | `default_resolution` | Độ phân giải mặc định: `1920x1080`, `1080x1920`, `3840x2160`, v.v. |

> **Lưu ý:** OpenMontage không tự cung cấp LLM. Bạn cần một trợ lý mã hóa AI (Claude Code, Cursor, v.v.) đã được cấu hình với key LLM riêng. `config.yaml` chỉ giúp agent biết nên gọi provider nào khi cần LLM trong các công cụ Python.

---

## Bước 9: Kiểm Tra Preflight & Khả Năng Hệ Thống

### 9.1 Kiểm tra danh sách công cụ sẵn sàng

```bash
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))"
```

Hoặc dùng Makefile:

```bash
make preflight
```

Kết quả sẽ hiển thị:

- `composition_runtimes` — FFmpeg, Remotion, HyperFrames có sẵn không
- `capabilities` — số lượng provider đã cấu hình cho từng nhóm (video gen, image gen, TTS, music)
- `setup_offers` — những công cụ chỉ cần set env var là dùng được
- `runtime_warnings` — cảnh báo về môi trường (ví dụ hyperframes không resolve được)

### 9.2 Kiểm tra chi tiết từng capability

```bash
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.capability_catalog(), indent=2))"
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_catalog(), indent=2))"
```

### 9.3 Kiểm tra Remotion

```bash
cd remotion-composer
npx remotion versions
cd ..
```

### 9.4 Kiểm tra HyperFrames

```bash
make hyperframes-doctor
```

Nếu tất cả đều OK, bạn đã sẵn sàng tạo video.

---

## Bước 10: Chạy Demo Không Cần API Key

Demo sử dụng Remotion để render các video thành phần như biểu đồ, text card, KPI grid — không cần API key trả phí.

```bash
make demo
```

Hoặc:

```bash
python render_demo.py
```

Xem danh sách demo có sẵn:

```bash
make demo-list
# hoặc
python render_demo.py --list
```

Nếu demo render thành công, file MP4 sẽ xuất hiện trong thư mục `output/` hoặc `remotion-composer/out/`.

---

## Bước 11: Bắt Đầu Sản Xuất Video

Sau khi setup xong, mở dự án trong trợ lý mã hóa AI (Claude Code, Cursor, Copilot, Windsurf, Codex) và nói yêu cầu của bạn.

### Ví dụ prompt

```text
"Make a 60-second animated explainer about how neural networks learn"
```

Hoặc:

```text
"Make a 75-second documentary montage about city life in the rain. Use real footage only, no narration, elegiac tone, with music."
```

Agent sẽ:

1. Chọn pipeline phù hợp (`animated-explainer`, `cinematic`, `hybrid`, v.v.)
2. Chạy preflight để kiểm tra công cụ
3. Trình bày concept, kế hoạch sản xuất, chi phí dự kiến
4. Chờ bạn phê duyệt trước khi tạo asset
5. Thực hiện từng stage: research → proposal → script → scene_plan → assets → edit → compose
6. Render video cuối cùng vào `projects/<project-name>/renders/final.mp4`

### Thư mục output

Mỗi production run tạo một workspace trong `projects/<project-name>/`:

```text
projects/<project-name>/
├── artifacts/          # JSON artifacts từ mỗi stage
├── assets/
│   ├── images/         # Ảnh đã tạo
│   ├── video/          # Clip video
│   ├── audio/          # Thuyết minh + mix
│   ├── music/          # Nhạc nền
│   └── subtitles.srt   # Phụ đề
└── renders/
    └── final.mp4       # Video hoàn chỉnh
```

Thư mục `projects/` đã được `.gitignore` bỏ qua — tất cả asset đều có thể tạo lại.

---

## Cài Đặt GPU (Tùy Chọn)

Nếu bạn có GPU NVIDIA và muốn tạo video miễn phí bằng mô hình local:

```bash
make install-gpu
```

Hoặc thủ công:

```bash
python -m pip install -r requirements-gpu.txt
python -m pip install diffusers transformers accelerate
```

Sau đó bật trong `.env`:

```bash
VIDEO_GEN_LOCAL_ENABLED=true
VIDEO_GEN_LOCAL_MODEL=wan2.1-1.3b  # hoặc: wan2.1-14b, hunyuan-1.5, ltx2-local, cogvideo-5b
```

> **Lưu ý:** Video generation local đòi hỏi VRAM lớn (thường ≥ 8 GB, tùy model). Mô hình 1.3B nhẹ nhất, 14B nặng nhất nhưng chất lượng cao hơn.

---

## Xử Lý Sự Cố Thường Gặp

### Lỗi: `'make' is not recognized as an internal or external command` (Windows)

**Giải pháp:** Cài `make` hoặc chạy lệnh thủ công:

```bash
python -m pip install -r requirements.txt
cd remotion-composer && npm install && cd ..
python -m pip install piper-tts
cp .env.example .env
```

### Lỗi: `npm install` báo `ERR_INVALID_ARG_TYPE`

**Giải pháp:**

```bash
cd remotion-composer
npx --yes npm install
cd ..
```

### Lỗi: `ModuleNotFoundError: No module named 'tools'`

**Giải pháp:** Chạy lệnh từ thư mục gốc `OpenMontage/`, không phải từ subfolder.

### Lỗi: `ffmpeg` không tìm thấy

**Giải pháp:** Thêm thư mục chứa `ffmpeg.exe` vào PATH, hoặc restart terminal sau khi cài FFmpeg.

### Lỗi: HyperFrames doctor báo fail

1. Kiểm tra Node.js ≥ 22.
2. Kiểm tra FFmpeg trong PATH.
3. Kiểm tra internet để npx tải package.
4. Thử chạy lại: `npx --yes --prefer-online hyperframes --version`

### Lỗi: Provider menu hiển thị 0/13 video generation configured

**Giải pháp:** Thêm ít nhất một key: `FAL_KEY`, `HEYGEN_API_KEY`, `RUNWAY_API_KEY`, hoặc bật `VIDEO_GEN_LOCAL_ENABLED=true` nếu có GPU. Nếu không, hãy chọn pipeline không cần video generation (ví dụ: dùng Remotion để animate ảnh tĩnh, hoặc dùng stock footage).

### Lỗi: Không có LLM trả lời

OpenMontage không tự cung cấp LLM. Đảm bảo trợ lý mã hóa AI của bạn (Claude Code, Cursor, v.v.) đã được cấu hình API key LLM. `config.yaml` chỉ định provider để agent gọi khi cần.

### Lỗi: `.env` không load

Đảm bảo file `.env` nằm ở thư mục gốc `OpenMontage/.env`. Không đổi tên thành `.env.local` hay đặt trong subfolder.

---

## Lệnh Makefile Tham Khảo

| Lệnh | Mô tả |
|---|---|
| `make setup` | Cài đặt toàn bộ một lần (Python, Remotion, Piper, HyperFrames warm, tạo `.env`) |
| `make install` | Cài Python requirements |
| `make install-dev` | Cài dependency phát triển |
| `make install-gpu` | Cài dependency GPU |
| `make test` | Chạy toàn bộ test |
| `make test-contracts` | Chạy contract test |
| `make lint` | Kiểm tra cú pháp các file Python lõi |
| `make preflight` | Hiển thị menu provider |
| `make hyperframes-doctor` | Kiểm tra HyperFrames runtime |
| `make hyperframes-warm` | Refresh cache HyperFrames qua npx |
| `make demo` | Render demo không cần API key |
| `make demo-list` | Liệt kê demo có sẵn |
| `make clean` | Xóa `__pycache__` và file `.pyc` |

---

## Tài Liệu Tham Khảo

| Tài liệu | Mục đích |
|---|---|
| [`README.md`](README.md) | Giới thiệu tổng quan dự án |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | Hợp đồng vận hành cho agent |
| [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) | Kiến trúc, convention, source of truth |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Kiến trúc kỹ thuật chi tiết |
| [`docs/PROVIDERS.md`](docs/PROVIDERS.md) | Hướng dẫn provider: setup, giá, free tier |
| [`docs/PR_REVIEW_GUIDE.md`](docs/PR_REVIEW_GUIDE.md) | Hướng dẫn review |
| [`PROMPT_GALLERY.md`](PROMPT_GALLERY.md) | Các prompt mẫu đã test |
| [`skills/INDEX.md`](skills/INDEX.md) | Index các skill cho agent |
| [`pipeline_defs/`](pipeline_defs/) | Các pipeline manifest |
| [`schemas/`](schemas/) | JSON schema kiểm tra artifact |

---

## ✅ Checklist Trước Khi Chạy Production

- [ ] Python 3.10+ đã cài
- [ ] FFmpeg đã cài và trong PATH
- [ ] Node.js 18+ (22+ cho HyperFrames) đã cài
- [ ] `pip install -r requirements.txt` thành công
- [ ] `cd remotion-composer && npm install` thành công
- [ ] `pip install piper-tts` thành công (hoặc đã có TTS cloud key)
- [ ] `npx --yes hyperframes --version` trả về version
- [ ] File `.env` đã tạo từ `.env.example`
- [ ] Ít nhất một key hữu ích đã được điền (FAL/GOOGLE/OPENAI) hoặc đã bật local GPU
- [ ] `make preflight` hiển thị menu provider không có lỗi nghiêm trọng
- [ ] `make demo` chạy thành công

---

Chúc bạn sản xuất video vui vẻ với OpenMontage! 🎬
