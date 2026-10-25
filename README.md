# Cyrene: AI companion untuk rumah pintar

Cyrene adalah AI companion bergaya karakter anime yang berjalan di PC Windows kamu.
Dia mendengarkan lewat satu mic USB, **hanya menanggapi suara pemiliknya** (Master),
ngobrol dalam bahasa Indonesia dengan selipan ungkapan Jepang, dan mengendalikan
rumah: lampu dan colokan Wi-Fi Tuya; AC, proyektor, dan TV lewat IR blaster; serta
PC itu sendiri (daya, musik, volume, dan aplikasi). Nama dan kepribadiannya bisa diganti
di `config.yaml` dan `config/persona.md`.

Yang baru di setiap versi, dengan cara mencobanya: [CHANGELOG.md](CHANGELOG.md).

Rekaman mic, deteksi bicara, verifikasi suara, dan speech-to-text diproses lokal di
PC (Whisper dan verifikasi suara memakai GPU). Otaknya bisa model lokal lewat
**Ollama** (gratis, offline) atau **Claude** lewat API.

```text
Master : "Cyrene, nyalakan lampu kamar."
Cyrene : "はい! Lampu kamar sudah menyala, Master."
Master : "Terus nyalakan TV juga."              (tanpa memanggil nama, masih dalam jendela lanjutan)
Master : "Cyrene, matikan komputer."
Cyrene : "Komputer mau kumatikan sekarang, Master? Jawab iya kalau jadi." (minta konfirmasi dulu)
```

## Daftar isi

- [Kebutuhan](#kebutuhan)
- [Arsitektur](#arsitektur)
- [Instalasi](#instalasi)
- [Setup pertama](#setup-pertama)
- [Memilih otak: Ollama atau Claude](#memilih-otak-ollama-atau-claude)
- [Menyambungkan perangkat](#menyambungkan-perangkat)
- [Mendaftarkan suara Master](#mendaftarkan-suara-master)
- [Pemakaian sehari-hari](#pemakaian-sehari-hari)
- [Respons instan](#respons-instan)
- [Kartu teks](#kartu-teks)
- [Jalan otomatis saat login](#jalan-otomatis-saat-login)
- [Versi baru](#versi-baru)
- [Suara anime (つくよみちゃん)](#suara-anime-つくよみちゃん)
- [Suara cadangan offline](#suara-cadangan-offline)
- [Avatar 3D (つくよみちゃん)](#avatar-3d-つくよみちゃん)
- [Avatar Live2D & interaksi](#avatar-live2d--interaksi)
- [Teman main gacha (HoYoverse)](#teman-main-gacha-hoyoverse)
- [Ingatan Cyrene](#ingatan-cyrene)
- [Jadwal, pengingat, dan rutinitas](#jadwal-pengingat-dan-rutinitas)
- [Alarm bangun](#alarm-bangun)
- [Daftar tugas](#daftar-tugas)
- [Musik, volume, dan aplikasi PC](#musik-volume-dan-aplikasi-pc)
- [Volume suaraku](#volume-suaraku)
- [Lagu di YouTube](#lagu-di-youtube)
- [Melihat layar](#melihat-layar)
- [Artinya apa](#artinya-apa)
- [Sapaan pagi dan ringkasan harian](#sapaan-pagi-dan-ringkasan-harian)
- [Peringatan hujan](#peringatan-hujan)
- [Cuaca beberapa hari](#cuaca-beberapa-hari)
- [Biaya API Claude](#biaya-api-claude)
- [Cyrene di HP](#cyrene-di-hp)
- [Kesehatan PC](#kesehatan-pc)
- [Jam main](#jam-main)
- [Teman fokus](#teman-fokus)
- [Mode game](#mode-game)
- [Pengingat saat game dibuka](#pengingat-saat-game-dibuka)
- [Pemantau internet](#pemantau-internet)
- [Jurnal harian](#jurnal-harian)
- [Rekap hari ini](#rekap-hari-ini)
- [Pencarian web dan promo gratis](#pencarian-web-dan-promo-gratis)
- [Keuangan dari tepira](#keuangan-dari-tepira)
- [Mode tamu](#mode-tamu)
- [Ulangi](#ulangi)
- [Belajar bahasa Jepang](#belajar-bahasa-jepang)
- [Pengingat istirahat](#pengingat-istirahat)
- [Waktu sholat](#waktu-sholat)
- [Tombol cepat di PC](#tombol-cepat-di-pc)
- [Diagnosa suara](#diagnosa-suara)
- [Hotkey global](#hotkey-global)
- [Kamus Cyrene](#kamus-cyrene)
- [Pemecahan masalah](#pemecahan-masalah)
- [Checklist uji perangkat keras](#checklist-uji-perangkat-keras)
- [Privasi](#privasi)
- [Pengembangan](#pengembangan)

## Kebutuhan

- **Windows 11** dan GPU NVIDIA. Proyek ini dikembangkan di RTX 5060 Ti 16 GB.
  Tanpa GPU kamu tetap bisa mencoba mode teks (`waifu chat --dry-run`); mode suara
  bisa dijalankan di CPU tapi jauh lebih lambat.
- **Python 3.14** (3.11 ke atas juga didukung) dengan launcher `py` dari python.org.
- Driver NVIDIA terbaru. Seri RTX 50 butuh torch build CUDA 12.8 ke atas (index
  `cu128` di bagian instalasi).
- Internet untuk instalasi, unduhan model pertama kali, dan suara edge-tts (tanpa internet
  dia bicara dengan [suara cadangan Windows](#suara-cadangan-offline)).
- Wi-Fi 2.4 GHz untuk perangkat Tuya dan Broadlink; PC dan semua perangkat ada di
  jaringan lokal yang sama.

### Perangkat keras (anggaran ≤ Rp1 juta)

Tidak ada hub yang menyala terus: PC yang menjalankan semuanya.

| Barang | Dipakai untuk | Catatan | Perkiraan harga |
|---|---|---|---|
| IR blaster: **Broadlink RM4 Mini** *atau* **Tuya IR universal remote (Wi-Fi)** | AC Samsung, proyektor Wanbo Dali, TV | Satu cukup untuk satu ruangan. Sinyal IR tidak tembus tembok, jadi letakkan di tempat yang "melihat" semua perangkatnya. | Broadlink sekitar Rp250–400 rb; Tuya IR sekitar Rp60–150 rb |
| **Lampu Wi-Fi Tuya** | Lampu kamar | Pilih yang bisa dipasang di aplikasi Smart Life / Tuya Smart dan memakai Wi-Fi, bukan Zigbee/Bluetooth (keduanya butuh hub). | sekitar Rp50–120 rb per lampu |
| **Colokan pintar (smart plug) Tuya** | Menyalakan PC (trik BIOS) atau alat lain | Cek rating watt/ampere-nya cukup untuk PC kamu. | sekitar Rp60–150 rb |
| **Mic USB** | Telinga Cyrene | Letakkan dekat tempat kamu biasa bicara dan jauh dari speaker. | sekitar Rp100–300 rb |

Semua harga di atas **perkiraan** dan berubah-ubah per toko dan waktu; cek sendiri
sebelum membeli. Contoh paket 1 IR blaster, 2 lampu, 1 colokan, dan 1 mic kira-kira
Rp300–850 rb dengan IR blaster Tuya, atau Rp500 rb–1,1 juta dengan Broadlink; pilih
barang yang lebih murah kalau ingin tetap di bawah Rp1 juta.

## Arsitektur

```text
 Mic USB
   │  audio (dibuka di sample rate bawaan mic, di-resample ke 16 kHz)
   ▼
 MicStream ──► EnergySegmenter: potong jadi satu ucapan utuh
   │
   ▼
 SpeakerVerifier (ECAPA, voiceprint di data/)      bukan suara Master → diabaikan
   │
   ▼
 WhisperStt (faster-whisper large-v3-turbo, GPU) → teks
   │
   ▼
 WakeGate ("Cyrene" di mana saja, atau jendela lanjutan 8 detik)
   │                                               tidak dipanggil → diabaikan
   ▼
 Brain: Ollama (qwen3:8b, lokal) atau Claude (API)
   │          │ tool call          ▲ hasil berhasil / gagal
   │          ▼                    │ (disampaikan dengan jujur)
   │      DeviceRegistry ──────────┘
   │        ├─ Tuya: lampu, colokan (LAN lokal)
   │        ├─ IR: Broadlink / Tuya ──► AC, proyektor, TV
   │        ├─ PC: shutdown, restart, sleep, Wake-on-LAN
   │        ├─ Media: tombol musik dan volume Windows
   │        └─ Aplikasi: membuka program dan game dari daftar di config.yaml
   │
   │  jawaban teks
   ▼
 SpeechRouter: bagian Indonesia dan Jepang dipisah, masing-masing
   │           pakai suaranya sendiri (edge-tts / GPT-SoVITS)
   │           (gagal → suara cadangan bawaan Windows, offline)
   ▼
 RVC (opsional): seluruh klip diubah ke suara つくよみちゃん oleh server Applio lokal
   │             (gagal → klip edge-tts aslinya yang diputar)
   ▼
 Avatar (opsional): maskot 3D (VRM) atau Live2D menggerakkan mulut mengikuti klip itu
   │                dan memasang ekspresi jawaban ([senang], [sedih], ...); elusan,
   │                colekan, dan klik dua kali di maskot sampai ke Cyrene lewat server lokal
   ▼
 Speaker  (mic dijeda selama dia bicara, jadi dia tidak mendengar dirinya sendiri)
```

Teman main gacha (opsional) menambah dua tool game ke otaknya (status HoYoLAB dan
riwayat pull) dan satu thread di `waifu run` yang mengecek game tiap 15 menit, lalu
mengingatkan lewat suara (di sela kalimatmu, tidak pernah memotong) dan notifikasi HP.

Ingatan jangka panjang menambah dua tool lagi (`remember` dan `forget`) yang mengubah
`data\memory.json`; isi file itu ditambahkan ke akhir system prompt di awal setiap sesi.

Jadwal menambah tool `schedule`, `list_schedules`, `cancel_schedule`, dan `run_routine`
(kalau ada rutinitas di `config.yaml`) yang mengubah `data\schedule.json`, dan satu thread di
`waifu run` yang mengecek file itu setiap 15 detik: pengingat diucapkan dan dikirim ke HP,
perintah perangkat dan rutinitas dijalankan lewat `DeviceRegistry` yang sama.

Daftar tugas menambah tool `add_task`, `list_tasks`, `complete_task`, `reopen_task`,
`remove_task`, dan `edit_task` yang mengubah `data\todo.json`; tugas yang tenggatnya hari ini
atau sudah lewat ikut masuk ke `daily_briefing` untuk sapaan pagi.

Lagu di YouTube menambah tool `play_youtube` yang membuka halaman lagu di YouTube di browser
default, hanya di giliran yang kalimat Master-nya meminta lagu atau musik, dan hanya alamat
YouTube.

Cyrene di HP (opsional) menambah server kedua di `127.0.0.1` selama `waifu run`, yang
dibagikan ke HP lewat `tailscale serve`: teks atau rekaman dari HP masuk ke otak yang sama
(satu giliran pada satu waktu, bergantian dengan mic PC), dan jawabannya disuarakan untuk HP,
tidak di speaker PC. Selama kamera di halaman HP terbuka, pesan itu membawa satu foto yang
masuk ke otak Claude bersama kalimatnya, hanya untuk giliran itu.

Kode ada di `src/waifu/`: `devices/`, `brain/`, `voice/`, `avatar/`, `gacha/`, `phone/`, lalu
`memory.py`, `schedule.py`, `routines.py`, `scheduler.py`, `todo.py`, `music.py`, `notify.py`,
`gate.py`, `app.py`, dan `cli.py` yang menyatukan semuanya. Detail kontrak tiap modul ada di
`SPEC.md`.

## Instalasi

Semua perintah dijalankan di **PowerShell** dari folder repo ini.

### 1. Paket inti

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\waifu --help
```

Paket inti sudah cukup untuk `waifu chat`, `say`, `devices`, `device`, `learn-ir`,
`mics`, dan `check`. Untuk mode suara (`run`, `enroll`, `speaker-test`) pasang juga
paket suara di bawah.

Supaya cukup mengetik `waifu`, aktifkan venv dulu:

```powershell
.venv\Scripts\Activate.ps1
waifu --help
```

Kalau PowerShell menolak menjalankan skrip aktivasi, tidak perlu mengubah
pengaturan apa pun: panggil saja langsung `.venv\Scripts\waifu`. Contoh perintah di
README ini memakai `waifu` dengan venv aktif.

### 2. Paket suara (STT, verifikasi suara)

```powershell
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python -m pip install -e ".[voice]"
```

- **Urutannya penting.** `speechbrain` membutuhkan torch; kalau `.[voice]` dipasang
  lebih dulu, pip mengambil torch versi CPU dari PyPI. Index `cu128` dibutuhkan
  untuk RTX seri 50.
- Cek hasilnya; harus muncul versi `+cu128` dan `True`:

  ```powershell
  .venv\Scripts\python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
  ```

- **Ruang disk:** perkiraan total 7–8 GB, termasuk model (torch CUDA sekitar 4 GB,
  model Whisper large-v3-turbo sekitar 1,6 GB, model ECAPA dan paket lain sisanya).
  Model Ollama dihitung terpisah (lihat di bawah).
- **Unduhan model pertama kali** masuk ke folder `models/` (tidak di-commit):
  - model verifikasi suara ECAPA ke `models\spkrec-ecapa`, saat pertama kali
    `waifu enroll`;
  - model Whisper ke `models\whisper`, saat `waifu run` pertama kali dijalankan
    (sebelum dia bilang siap). Start pertama setelah instalasi akan lama karena
    unduhan ini.

### 3. Konfigurasi

```powershell
Copy-Item config.example.yaml config.yaml
notepad config.yaml
waifu check
```

Mic, perangkat Tuya, IR blaster, dan notifikasi HP tidak perlu diisi dengan tangan: lanjutkan
dengan `waifu setup` (lihat [Setup pertama](#setup-pertama)).

`config.yaml` tidak di-commit karena berisi local key perangkat. Bagian-bagiannya:

| Bagian | Isi |
|---|---|
| `assistant` | nama, `owner_call` (panggilannya untukmu), `wake_words`, `followup_seconds`, `persona_file`, kapan obrolan direset |
| `audio` | `input_device` (satu mic, atau beberapa berurutan; nama dari `waifu mic`, atau nomor atau nama dari `waifu mics`), `output_device`, `vad` (kepekaan deteksi bicara), `override_path` (mic dan setelan dari [halaman Suara di HP](#diagnosa-suara)) |
| `speaker` | verifikasi suara: `enabled`, `voiceprint_path`, `threshold`, `device` |
| `stt` | model Whisper, `device`, `compute_type`, `languages` |
| `brain` | `backend` (`ollama` atau `claude`), `max_tool_rounds`, lalu blok `ollama` dan `claude` |
| `tts` | suara per bahasa (`id`, `ja`): edge-tts atau GPT-SoVITS; `rvc` untuk [suara anime](#suara-anime-つくよみちゃん); `fallback` untuk [suara cadangan offline](#suara-cadangan-offline) |
| `ir_codes_path` | file kode IR hasil belajar (default `data/ir_codes.json`) |
| `devices` | daftar perangkat (lihat [Menyambungkan perangkat](#menyambungkan-perangkat)) |

Kunci yang tidak dikenal ditolak dengan pesan yang jelas, jadi salah ketik langsung
ketahuan saat `waifu check`. Kepribadian Cyrene ada di `config/persona.md`.

Ada juga persona yang lebih mirip Cyrene di Honkai: Star Rail, `config/persona_hsr.md`:
sifatmu sendiri tetap ada (tenang, puitis, sedikit manja, suka menggoda), ditambah sifat
Cyrene di game (berbelas kasih, ceria dan penuh harapan, menghargai hal-hal kecil dan
kenangan, bicara anggun seperti bersenandung) tanpa membeberkan cerita Amphoreus kecuali kamu
yang membahasnya. Aturan lainnya (suara, ekspresi, rumah, konfirmasi, game) sama persis.
Untuk mencobanya, tulis di `config.yaml`:

```yaml
assistant:
  persona_file: config/persona_hsr.md
```

lalu mulai ulang `waifu run`. Hapus baris itu untuk kembali ke persona semula.

`assistant.owner_call` (bawaan `Master`, misalnya `Sayang`) adalah panggilannya untukmu.
Semua kalimatnya memakai kata itu: jawaban, persona (`{owner}`), pertanyaan konfirmasi,
pengingat dan pengumuman jadwal atau game, permintaan maaf, sapaan saat `run --announce`,
dan kalimat reaksi avatar. "Iya, Sayang" tetap dihitung sebagai jawaban iya. Kata itu
dipakai persis seperti ditulis, kecuali di awal kalimat huruf pertamanya dibuat kapital
(`sayang` jadi "Sayang, jadwal ... gagal", tetapi tetap "Sudah, sayang.").

## Setup pertama

`config.yaml` yang baru disalin masih berisi contoh: perangkat Tuya dengan
`isi_dari_tinytuya_wizard`, IR blaster di `192.168.1.50`, tanpa mic dan tanpa notifikasi HP.
`waifu setup` memandumu mengisi keempatnya langsung di terminal, dalam bahasa Indonesia, lalu
menuliskannya sendiri ke `config.yaml`:

```powershell
waifu setup          # keempat langkah berurutan, lalu waifu check
waifu setup mic      # atau satu langkah saja: mic, tuya, ir, atau notify
```

| Langkah | Yang ditanyakan | Yang ditulis |
|---|---|---|
| `mic` | Tiap mic didengarkan 3 detik selagi kamu menyebut "halo Cyrene"; pilih nomor mic yang paling jelas (Enter = yang teratas), atau beberapa nomor berurutan (`1,2`) supaya yang berikutnya jadi cadangan kalau yang pertama terputus | `audio.input_device` |
| `tuya` | Wizard tinytuya dijalankan di terminalmu (kamu mengetik API Key dan API Secret sendiri ke wizard), lalu tiap lampu, colokan, dan IR blaster Tuya di `config.yaml` dicocokkan dengan perangkat dari wizard menurut namanya (lihat [Lampu dan colokan Tuya](#lampu-dan-colokan-tuya)) | `device_id`, `local_key`, `address`, `version` |
| `ir` | Alamat IP tiap IR blaster Broadlink (Enter = tetap), lalu perintah `waifu learn-ir` untuk tombol remote yang belum dipelajari | `ir.host` semua perangkat di blaster itu |
| `notify` | Nyalakan notifikasi HP? Topik ntfy acak dibuat, lalu langkah berlangganan di aplikasi ntfy; notifikasi tes hanya kalau kamu jawab `y` | `notify.enabled`, `notify.topic` |

- Setiap langkah bisa dilewati: `n`, atau `l` saat diminta nomor. Ctrl+C menghentikan setup.
  Menjalankannya lagi aman: yang sudah terisi ditunjukkan dan tetap, kecuali kamu memilih
  menggantinya. `device_id` yang sudah asli hanya diganti kalau kamu jawab `y`, dan topik ntfy
  hanya diganti kalau kamu minta topik baru (HP lalu harus berlangganan topik baru itu).
- Hanya kunci yang disebut yang diubah: komentar, urutan, tanda kutip, dan akhir baris
  `config.yaml` tetap. Sebelum menulis, isi lamanya disalin ke
  `data\config.yaml.bak-<tanggal>-<jam>`. Sesudahnya `config.yaml` dibaca ulang dan
  dibandingkan seluruhnya; kalau hasilnya tidak persis seperti yang dimaksud, isi lama
  dikembalikan.
- Kalau `config.yaml`-mu ditulis dalam bentuk yang tidak bisa diubah per baris (misalnya
  `tuya: {...}` dalam satu baris, tab, atau anchor YAML), tidak ada yang diubah: setup
  menampilkan potongan YAML untuk kamu tempel sendiri. Local key tidak pernah ditampilkan;
  salin nilai `"key"` dari `devices.json` untuk perangkat dengan `"id"` yang sama.
- Di akhir, setup merangkum tiap langkah (ditulis, tidak diubah, atau gagal) lalu menulis
  **Langkah berikutnya**, misalnya `waifu enroll` setelah mic diganti, `waifu setup tuya`
  untuk perangkat yang belum diisi, `waifu learn-ir ac --missing`, atau `waifu setup notify`
  kalau notifikasi HP masih mati. Dengan `waifu setup` (semua langkah), rangkuman ini muncul
  sebelum `waifu check`.
- Perubahan dipakai setelah `waifu run` dijalankan ulang; setup mengingatkan kalau
  `waifu run` sedang berjalan. Setelah langkah `mic`, daftarkan ulang suaramu dengan
  `waifu enroll` lewat mic itu. Mic yang dipilih di halaman Suara di HP tetap dicoba lebih
  dulu daripada `audio.input_device`, dan setup mengatakannya.
- `waifu check` menandai yang masih contoh dengan `!`: perangkat Tuya dengan
  `isi_dari_tinytuya_wizard` ("Jalankan: waifu setup tuya") dan IR blaster di `192.168.1.50`
  ("waifu setup ir"; abaikan kalau blastermu memang di alamat itu).

## Memilih otak: Ollama atau Claude

Pilih dengan `brain.backend` di `config.yaml`.

### Ollama (lokal, gratis)

1. Pasang Ollama dari [ollama.com](https://ollama.com). Aplikasinya berjalan di
   latar belakang (ikon di tray).
2. Unduh modelnya (sekitar 5 GB, disimpan di folder Ollama sendiri):

   ```powershell
   ollama pull qwen3:8b
   ```

3. Konfigurasi bawaan sudah memakai Ollama:

   ```yaml
   brain:
     backend: ollama
     ollama:
       host: http://127.0.0.1:11434
       model: qwen3:8b
       think: false       # false = lebih cepat untuk percakapan suara
       keep_alive: 30m    # model tetap di VRAM selama 30 menit setelah dipakai
   ```

4. `waifu check` harus menampilkan `✓ Ollama siap dengan model qwen3:8b`.

### Claude (API, berbayar per pemakaian)

1. Atur kunci API sekali (berlaku untuk terminal yang dibuka setelahnya):

   ```powershell
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```

   Atau, kalau kamu memakai CLI `ant`, login dengan `ant auth login`; SDK Anthropic
   otomatis memakai profil aktif itu kalau `ANTHROPIC_API_KEY` tidak diatur.
   Jangan menaruh kunci API di `config.yaml`.

   Kalau Claude menjawab "API key ini belum terikat ke workspace" (HTTP 400 yang
   menyebut `anthropic-workspace-id`), kuncimu adalah kunci level organisasi. Pilih salah satu:
   buat API key baru dari dalam sebuah workspace di Claude Console, atau tambahkan juga
   variabel `ANTHROPIC_WORKSPACE_ID` berisi ID workspace (berawalan `wrkspc_`). Cyrene
   mengirimkannya sebagai header di setiap request, dan `waifu check` menampilkannya.
   Terminal yang dibuka sebelum variabel ditambahkan tidak melihatnya sampai dibuka ulang.
2. Ubah backend:

   ```yaml
   brain:
     backend: claude
     claude:
       model: claude-opus-5-5
       effort: low        # low = paling cepat untuk suara; pilihan lain: medium, high, xhigh, max
       max_tokens: 16000
       fallbacks: true
   ```

   `fallbacks: true` mengaktifkan fallback di sisi server: kalau model yang diminta
   menolak menjawab karena alasan kebijakan, server mencoba model pengganti.
3. `waifu check` menampilkan status kunci API.

Hanya dengan Claude, Cyrene bisa melihat layar saat Master memintanya (lihat
[Melihat layar](#melihat-layar)) dan mencari di web (lihat
[Pencarian web dan promo gratis](#pencarian-web-dan-promo-gratis)). Dia juga mencatat perkiraan biaya setiap jawabannya dan
bisa memberi tahu saat anggaran bulanan hampir habis (lihat
[Biaya API Claude](#biaya-api-claude)).

## Menyambungkan perangkat

Setiap perangkat di `devices` punya:

- `id`: huruf kecil, angka, atau garis bawah (`lampu_kamar`). Dipakai di CLI dan
  oleh Cyrene saat memanggil tool.
- `name` dan `aliases`: cara kamu menyebutnya ("lampu", "lampu utama").
- `type`: `tuya_light`, `tuya_plug`, `ir`, `pc`, `media`, `apps`, atau `mock`, lalu blok
  koneksinya (`tuya`, `ir`, atau `pc`). `media` tidak butuh blok apa pun dan `apps` berisi
  daftar aplikasinya; lihat [Musik, volume, dan aplikasi PC](#musik-volume-dan-aplikasi-pc).

Setelah mengubah perangkat, cek dengan `waifu devices` dan `waifu check`.

### Lampu dan colokan Tuya

1. Pasang semua lampu, colokan, dan IR blaster Tuya di aplikasi **Smart Life**
   atau **Tuya Smart** (Wi-Fi 2.4 GHz).
2. Buat akun developer di Tuya IoT Platform dan hubungkan akun aplikasimu, mengikuti
   panduan tinytuya:
   <https://github.com/jasonacox/tinytuya#get-the-tuya-device-local-key>. Singkatnya: di
   <https://iot.tuya.com> buat Cloud Project (Smart Home, data center yang sama dengan akun
   Smart Life-mu), lalu Devices > Link App Account dan pindai QR-nya dari aplikasi Smart Life.
   Yang disalin dari halaman Overview project: Access ID/Client ID (API Key) dan Access
   Secret/Client Secret (API Secret); Device ID yang ditanyakan wizard ada di tab Devices
   project itu.
   - Kalau tab Devices tetap kosong setelah QR dipindai, data center project-nya salah: ubah
     data center project (atau buat project baru) sampai perangkatmu muncul.
   - Layanan IoT Core project gratis tapi berlaku terbatas (awalnya 1 bulan). Sesudah habis,
     wizard tidak bisa mengambil daftar perangkat ("Error from Tuya server", kadang dengan
     "subscription has expired"); perpanjang di <https://iot.tuya.com> (1, 3, atau 6 bulan,
     cukup mengisi formulir).
3. Dari folder proyek, jalankan `waifu setup tuya`. Cyrene menjelaskan langkah di atas, lalu
   menjalankan wizard tinytuya (`python -m tinytuya wizard`) di terminalmu: kamu mengetik API
   Key, API Secret, satu Device ID (atau `scan`), dan region langsung ke wizard, dan Cyrene
   tidak membacanya. Wizard menampilkan API Secret dan semua local key di layar, jadi jangan
   merekam atau membagikan layar. Jawab Y untuk "Poll local devices?" supaya IP dan versi tiap
   perangkat ditemukan (wizard memindai jaringan rumahmu); n membiarkan `address: Auto`.
   Kalau wizard tidak mendapat daftar perangkat dari cloud (pesan Inggris seperti "Error from
   Tuya server ... Check API Key and Secret"), `devices.json` tidak ditulis ulang dan tidak ada
   yang diimpor, juga dari `devices.json` lama; Cyrene menjelaskan arti pesannya. Ctrl+C di
   wizard menghentikan setup.
4. Cyrene lalu mencocokkan tiap lampu, colokan, dan IR blaster Tuya di `config.yaml` dengan
   perangkat dari wizard menurut namanya. Tekan Enter untuk setuju, ketik nomor lain untuk
   memilih, atau `l` untuk melewati. `device_id`, `local_key`, `address` (kalau IP-nya
   ditemukan), dan `version` ditulis ke `config.yaml` (isi lamanya disalin ke `data\` dulu),
   dan local key tidak pernah ditampilkan. Hasilnya, sama dengan kalau diisi sendiri dari
   `devices.json` (`id`, `key`, `ip`, `version`):

   ```yaml
   - id: lampu_kamar
     name: Lampu kamar
     aliases: [lampu, lampu utama]
     type: tuya_light        # tuya_plug untuk colokan
     tuya:
       device_id: "bf1234567890abcdef"   # "id" dari devices.json
       local_key: "a1b2c3d4e5f6g7h8"     # "key" dari devices.json
       address: 192.168.1.60             # "ip"; atau Auto (mencari di jaringan, lebih lambat)
       version: 3.3                      # "version"
   ```

5. Terakhir Cyrene menawarkan menghapus `tinytuya.json` dan `tuya-raw.json`, yang berisi API
   Key dan API Secret akun Tuya-mu (dan jawaban lengkap cloud, termasuk semua local key);
   Cyrene tidak membutuhkannya, tapi wizard berikutnya akan menanyakan keduanya lagi. Wizard
   menulis keempat filenya (`tinytuya.json`, `devices.json`, `snapshot.json`, `tuya-raw.json`)
   ke folder proyek, dan semuanya ada di `.gitignore`. `devices.json` dan `snapshot.json` tetap
   berisi local key: jangan dibagikan. Menjalankan `waifu setup tuya` lagi membaca
   `devices.json` yang ada, kecuali kamu minta wizard dijalankan lagi.

Catatan versi protokol:

- Kebanyakan lampu dan colokan memakai **3.3**. Firmware yang lebih baru bisa memakai
  **3.4** atau **3.5**. Pakai angka yang ditemukan wizard saat memindai jaringan.
- Kalau versi atau local key salah, perintah gagal dengan pesan
  "... menolak koneksi. Cek local_key dan version-nya di config.yaml."
- **Local key berubah setiap kali perangkat dipasang ulang** di aplikasi. Jalankan
  `waifu setup tuya` lagi dan minta wizard dijalankan setelah reset atau pairing ulang:
  perangkat yang sama (`device_id` sama) langsung mendapat local key barunya. Kalau layanan
  IoT Core project-mu sudah habis, perpanjang dulu di <https://iot.tuya.com>; tanpa itu wizard
  tidak mendapat daftar baru dan tidak ada yang diimpor.
- Beri perangkat IP tetap (reservasi DHCP di router) supaya `address` tidak berubah.

Coba langsung dari CLI:

```powershell
waifu device lampu_kamar on
waifu device lampu_kamar brightness 40
waifu device lampu_kamar off
```

### IR blaster dan remote (AC, proyektor, TV)

**Broadlink RM4 Mini.** Pasang dulu di aplikasi Broadlink, pastikan perangkat
**tidak dikunci** (opsi lock di aplikasi Broadlink dimatikan) supaya bisa
dikendalikan dari LAN, lalu beri IP tetap. Cari IP-nya dengan:

```powershell
.venv\Scripts\python -c "import broadlink; [print(d.host[0], d.model, 'terkunci' if d.is_locked else 'tidak terkunci') for d in broadlink.discover(timeout=5)]"
```

```yaml
ir:
  backend: broadlink
  host: 192.168.1.50
```

`waifu setup ir` menanyakan alamat ini dan menuliskannya ke semua perangkat di blaster itu
(Cyrene tidak mencarinya sendiri di jaringan), lalu menyebut perintah `waifu learn-ir` untuk
tombol yang belum dipelajari.

**Tuya IR universal remote.** Ambil `device_id`/`local_key`-nya dengan `waifu setup tuya`
seperti di atas (IR blaster ikut dicocokkan), atau isi sendiri:

```yaml
ir:
  backend: tuya
  tuya:
    device_id: "bf..."
    local_key: "..."
    address: Auto
    version: 3.3
```

Semua perangkat IR yang memakai blaster yang sama berbagi satu koneksi.

#### Mempelajari tombol remote

Daftar `commands` di tiap perangkat IR adalah tombol yang **ingin** kamu ajarkan.
Cyrene hanya memakai tombol yang sudah benar-benar dipelajari.

```powershell
waifu learn-ir proyektor --missing   # ajarkan semua yang belum ada, satu per satu
waifu learn-ir proyektor power       # ajarkan atau ulangi satu tombol
waifu learn-ir tv --missing --timeout 30
waifu check                          # daftar tombol yang belum dipelajari
```

Untuk setiap tombol, arahkan remote ke blaster dari jarak dekat lalu tekan tombolnya
dalam waktu tunggu (default 20 detik). Kode tersimpan di `data/ir_codes.json`.
Tombol yang terlewat bisa diulang dengan `waifu learn-ir <id> --missing`. Untuk AC,
ikuti urutan di [Catatan AC Samsung](#catatan-ac-samsung).

**Aturan nama:** huruf kecil, angka, dan garis bawah. Untuk nilai angka pakai pola
`<perintah>_<angka>`: kalau Cyrene memanggil `ac` + `temp` + 24, yang dikirim adalah
kode `temp_24`. Dari CLI, `waifu device ac temp 24` sama dengan
`waifu device ac temp_24`.

#### Catatan AC Samsung

- **Remote AC mengirim seluruh keadaan sekaligus** (nyala, mode, suhu, kipas) di
  setiap tekanan tombol, bukan hanya "naik satu derajat". Jadi kode `temp_24` berarti
  "AC menyala, mode dingin, 24°C, kipas seperti saat dipelajari".
- Sebelum belajar, atur remote ke **mode Cool** dan kipas yang kamu suka.
- `learn-ir` langsung menunggu tombol berikutnya setelah satu kode tersimpan, jadi
  tekanan tombol untuk menyiapkan remote ikut tertangkap. Urutan yang aman:
  1. `waifu learn-ir ac on`: dengan layar remote mati, tekan **Power**.
  2. `waifu learn-ir ac off`: tekan **Power** sekali lagi.
  3. Nyalakan lagi layar remote dan atur ke **17** (arahkan menjauh dari AC kalau
     tidak ingin AC ikut menyala).
  4. `waifu learn-ir ac --missing`: yang tersisa `temp_16` sampai `temp_30`,
     berurutan. Untuk `temp_16` tekan **▼** sekali (17 ke 16), lalu untuk setiap
     suhu berikutnya tekan **▲** sekali.
- Satu suhu bisa diulang kapan saja: atur remote satu derajat di bawahnya (misalnya
  23), jalankan `waifu learn-ir ac temp_24`, lalu tekan **▲** sekali.
- Karena tiap kode membawa keadaan lengkap, mengirim kode suhu biasanya juga
  menyalakan AC kalau sedang mati.

#### Catatan proyektor Wanbo Dali

- Tombol power proyektor adalah **toggle**: kode yang sama untuk menyalakan dan
  mematikan, jadi cukup satu perintah `power` (sudah ada di contoh config).
- Kalau proyektor menampilkan dialog konfirmasi saat dimatikan, tombol `ok` (juga
  sudah ada di contoh config) bisa dipakai untuk mengonfirmasinya.
- Kalau ada tombol yang tidak pernah tertangkap saat `learn-ir`, kemungkinan tombol
  itu tidak memakai IR (misalnya lewat Bluetooth) dan tidak bisa diajarkan ke IR
  blaster.

#### Menambah TV

Contoh config belum berisi TV. Tambahkan blok seperti ini (dibutuhkan untuk user
story 3 di checklist), lalu `waifu learn-ir tv --missing`:

```yaml
- id: tv
  name: TV
  aliases: [tv, televisi]
  type: ir
  ir:
    backend: broadlink
    host: 192.168.1.50
  commands: [power, volume_up, volume_down, mute]
```

### Menyalakan dan mematikan PC

Perangkat `komputer` (`type: pc`, `local: true`) adalah PC yang menjalankan Cyrene:

| Perintah | Yang terjadi |
|---|---|
| `shutdown` | Windows dijadwalkan mati dalam `shutdown_delay_s` detik (default 30), lewat `shutdown /s /t 30` |
| `restart` | sama, lewat `shutdown /r /t 30` |
| `cancel_shutdown` | membatalkan jadwal di atas (`shutdown /a`) |
| `sleep` | PC masuk sleep sekitar 5 detik kemudian, supaya Cyrene sempat selesai bicara |

**Aturan keselamatan:** sebelum mematikan, me-restart, atau menidurkan PC (`shutdown`,
`restart`, `sleep`), Cyrene **bertanya dulu**, dan perintahnya baru jalan setelah kamu
menjawab ya. Ini dikunci di kode, bukan sekadar sifat model: perintah pertama selalu
ditahan, dan pertanyaannya diucapkan oleh kode sendiri di akhir jawabannya, jadi model
yang lupa bertanya atau salah dengar tidak bisa mematikan PC, dan kamu selalu tahu apa
yang akan dijalankan oleh "iya"-mu.

1. "Cyrene, matikan komputer." Dia belum mematikan apa pun dan bertanya "Komputer mau
   kumatikan sekarang, Master? Jawab iya kalau jadi." (untuk restart "kunyalakan
   ulang", untuk sleep "kutidurkan").
2. Jawab di ucapanmu berikutnya, paling lambat sekitar 40 detik setelah dia selesai
   bertanya: batasnya satu menit sejak perintahnya ditahan, dan waktu dia menyusun lalu
   mengucapkan pertanyaannya ikut terhitung. Dalam 8 detik pertama setelah dia selesai
   bicara, "iya" saja sudah cukup; setelah itu "Cyrene, iya". Kalau pertanyaannya kurang
   jelas, "ulangi" mengulangnya tanpa dihitung sebagai jawaban (lihat [Ulangi](#ulangi)).
3. Jawaban dihitung **ya** hanya kalau berupa "iya" yang polos: diawali kata seperti
   *iya*, *ya*, *boleh*, *oke*, *lanjut*, *betul*, *silakan*, *gas*, *yakin*, *siap*,
   *yaudah*, atau はい, lalu paling banyak diikuti kata yang mengulang perintahnya
   (*matikan*, *matiin*, *restart*, *sleep*, *tidurkan*, *komputernya*, *colokannya*,
   *sekarang*, *aja*, *deh*, *dong*, *udah*), "Master", atau nama Cyrene. "Iya, matikan",
   "Cyrene, boleh", dan "ya udah, matiin aja" adalah ya. Kalimat lain **bukan** ya,
   walaupun ada "ya"-nya: "oke makasih ya", "nyalakan lampu kamar ya", "iya ntar",
   "bentar ya, aku save dulu", "ya kali, aku lagi main game", "ya udah, lampunya aja",
   "eh jangan, tunggu", "kurang yakin", dan pertanyaan seperti "iya?".
4. Selain ya, PC tetap menyala: "tidak" atau kalimat lain, "iya" yang terlambat (lewat
   batas di langkah 2, atau baru di ucapan sesudahnya), dan "iya" yang diucapkan
   bersamaan dengan perintahnya ("iya, matikan komputer sekarang") tidak dihitung. Kalau
   masih mau, ulangi perintahnya dari awal.

Yang dihitung hanya kata-katamu sendiri seperti yang didengar Cyrene (atau yang kamu
ketik di `waifu chat`); model tidak bisa mengiyakan atas namamu, dan satu "iya" hanya
berlaku sekali, untuk perintah yang dia tanyakan. Sekali bertanya hanya untuk satu
perintah: "matikan komputer dan colokannya" ditanyakan satu per satu, dan masing-masing
butuh "iya"-nya sendiri. Kalau jawabannya batal di tengah jalan (misalnya koneksi ke
Claude atau Ollama putus dan dia hanya minta maaf), perintahnya ikut dilupakan; ulangi
dari awal. `wake` dan `cancel_shutdown` tidak perlu konfirmasi.

Setelah shutdown atau restart diiyakan, jeda 30 detik tetap berlaku; bilang
**"Cyrene, batalkan"** untuk membatalkan (dalam 8 detik pertama setelah dia selesai
bicara, "batalkan" saja sudah cukup). Kamu juga bisa membatalkan manual dengan
`shutdown /a` atau `waifu device komputer cancel_shutdown`.

> Perintah dari terminal tidak ditanya lagi: `waifu device komputer shutdown` langsung
> menjadwalkan shutdown (dengan jeda 30 detik yang sama), karena kamu sendiri yang
> mengetiknya. Jadwal dan rutinitas tidak pernah mematikan PC sama sekali.

**Menyalakan PC** ada di luar loop suara, karena Cyrene berjalan di PC itu sendiri.
Pilih salah satu:

**A. Colokan pintar + BIOS.**

1. Di BIOS, ubah **"Restore on AC Power Loss"** menjadi **"Power On"**. Namanya beda
   per merek motherboard (misalnya "AC Power Recovery", "Restore AC Power Loss",
   "Power On After Power Fail"), biasanya di menu Power, APM, atau Advanced.
2. Colokkan PC ke smart plug Tuya.
3. Matikan Windows seperti biasa, lalu matikan colokan dari aplikasi Smart Life.
4. Saat ingin menyalakan PC, nyalakan colokan dari aplikasi: listrik kembali, PC
   menyala sendiri.

> Jangan pernah mematikan colokan selagi Windows masih menyala; itu sama dengan
> mencabut kabel. Contoh config berisi `colokan_pc` supaya terlihat di
> `waifu devices`; kalau khawatir salah perintah suara, hapus blok itu dari
> `config.yaml` dan kendalikan colokan hanya dari aplikasi. `powers_pc: true` pada
> `colokan_pc` membuat Cyrene menjaganya seperti PC: mematikan atau membaliknya (`off`,
> `toggle`) lewat suara juga ditahan kode sampai kamu menjawab ya, dengan aturan yang sama
> seperti di atas (`on` tidak perlu konfirmasi); pertanyaannya "Colokan PC mau kumatikan
> sekarang, Master? Listrik PC bisa langsung putus. Jawab iya kalau jadi." Bedanya, colokan
> tidak punya jeda 30 detik dan tidak bisa dibatalkan: begitu kamu menjawab ya, listriknya
> langsung putus, jadi jangan mengiyakannya selagi Windows masih menyala atau sedang
> shutdown. Colokan itu
> juga tidak pernah bisa dijadwalkan atau jadi langkah rutinitas (lihat
> [Cara jadwal dijalankan](#cara-jadwal-dijalankan)), sedangkan `waifu device colokan_pc off`
> dari terminal langsung mematikannya tanpa bertanya. Tandai juga colokan lain yang
> mengalirkan listrik ke PC dengan `powers_pc: true`.

**B. Wake-on-LAN.**

1. Aktifkan "Wake on LAN" (kadang bernama "Power On By PCI-E") di BIOS.
2. Di Device Manager, buka adapter jaringan: tab Power Management centang izin
   membangunkan komputer; tab Advanced aktifkan "Wake on Magic Packet".
3. Paling andal lewat kabel LAN. Kalau PC tidak bangun dari shutdown, mematikan
   Fast Startup Windows sering membantu.
4. Catat alamat MAC dengan `getmac /v`, lalu kirim magic packet dari aplikasi
   Wake-on-LAN di HP, atau dari Cyrene di PC lain dengan perangkat seperti ini
   (`waifu device pc_utama wake`):

   ```yaml
   - id: pc_utama
     name: PC utama
     aliases: [pc utama]
     type: pc
     pc:
       local: false
       mac: "AA:BB:CC:DD:EE:FF"
       broadcast: 192.168.1.255
   ```

## Mendaftarkan suara Master

Cyrene hanya menanggapi suara yang cocok dengan voiceprint Master. Daftarkan di
ruangan dan dengan mic yang sama seperti pemakaian sehari-hari.

```powershell
waifu setup mic       # mic mana yang mendengar suaramu, ditulis ke audio.input_device
waifu enroll          # 6 rekaman x 4 detik; bacakan kalimat yang muncul
waifu speaker-test    # skor kecocokan langsung, Ctrl+C untuk berhenti
```

- `waifu enroll` menghitung mundur sebelum tiap rekaman, menyimpan voiceprint ke
  `data/voiceprint.npy`, lalu menampilkan skor tiap rekaman dan **saran threshold**.
  Jumlah dan durasi rekaman bisa diubah: `waifu enroll --samples 8 --seconds 5`
  (minimal 3 rekaman).
- `waifu speaker-test` menampilkan `✓` atau `✗` dan skornya untuk setiap ucapan,
  misalnya `✓ skor 0.62 (2.1 detik)`. Bicaralah beberapa kali seperti biasa, lalu
  minta orang lain bicara juga.
- **Menyetel threshold:** pilih `speaker.threshold` di antara skor terendah suaramu
  dan skor tertinggi suara orang lain. Terlalu tinggi berarti suaramu sering
  ditolak; terlalu rendah berarti tamu bisa ikut memerintah.
- Daftar ulang kalau kamu pindah ruangan, ganti mic, atau mengubah posisi mic.
- Untuk sementara melayani semua suara: `waifu run --no-speaker-check`, atau
  `speaker.enabled: false` di config.

## Pemakaian sehari-hari

| Perintah | Kegunaan |
|---|---|
| `waifu run` | Mode suara: muat model, lalu dengarkan mic dan jawab dengan suara (Ctrl+C untuk berhenti) |
| `waifu run --dry-run` | Mode suara dengan perangkat simulasi dan jadwal sementara, tanpa menyentuh perangkat atau jadwal asli |
| `waifu run --no-speaker-check` | Layani semua suara, bukan hanya Master |
| `waifu run --startup-delay 15 --announce` | Tunggu 15 detik dulu, lalu setelah model dimuat dia bilang "Cyrene siap, Master." (dipakai autostart) |
| `waifu autostart enable` / `disable` / `status` | Jalan otomatis saat login Windows (lihat [Jalan otomatis saat login](#jalan-otomatis-saat-login)) |
| `waifu version` | Versi Cyrene di disk dan versi `waifu run` yang sedang berjalan, dengan perintah yang perlu dijalankan kalau dia perlu dimulai ulang atau paketnya berubah (lihat [Versi baru](#versi-baru)) |
| `waifu chat` | Ngobrol lewat teks; ketik `keluar` untuk berhenti |
| `waifu chat --dry-run -v` | Uji persona dan tool call ke perangkat simulasi, dengan jadwal sementara (`-v` menampilkan setiap tool call) |
| `waifu chat --speak` | Ngobrol lewat teks, jawabannya juga diucapkan |
| `waifu say "Halo Master, おかえりなさい!"` | Uji suara: bagian Indonesia lalu Jepang |
| `waifu say --raw "Halo Master"` | Sama, tapi tanpa suara anime (RVC), untuk membandingkan |
| `waifu say --fallback "Halo Master"` | Sama, tapi dengan suara cadangan offline (suara Windows) yang dipakai kalau edge-tts gagal (lihat [Suara cadangan offline](#suara-cadangan-offline)) |
| `waifu say --volume 150 "Halo Master"` | Sama, tapi dengan volume suaranya 10 sampai 150 persen untuk mencoba, tanpa menyimpannya (tanpa `--volume`: volume suaranya sekarang; lihat [Volume suaraku](#volume-suaraku)) |
| `waifu devices` | Daftar perangkat dan perintah yang tersedia |
| `waifu device <id> <perintah> [angka]` | Jalankan satu perintah, misalnya `waifu device ac temp 24` |
| `waifu learn-ir <id> <perintah>` / `waifu learn-ir <id> --missing` | Pelajari tombol remote IR |
| `waifu enroll` / `waifu speaker-test` | Daftarkan suara / cek skor kecocokan |
| `waifu mics` | Daftar perangkat audio dan nomornya |
| `waifu mic` / `waifu mic --seconds 5` | Cari mic yang mendengar suaramu: tiap mic didengarkan sebentar sambil kamu menyebut "halo Cyrene", lalu baris `audio.input_device` untuk mic terbaik (lihat [Pemecahan masalah](#pemecahan-masalah)) |
| `waifu setup` / `waifu setup mic`, `tuya`, `ir`, `notify` | Panduan setup pertama: mic, perangkat Tuya (lewat wizard tinytuya), alamat IR blaster, dan notifikasi HP, ditulis langsung ke `config.yaml` dengan cadangan di `data\`; tanpa langkah, keempatnya lalu `waifu check` (lihat [Setup pertama](#setup-pertama)) |
| `waifu voices` | Daftar suara Windows per bahasa, dan suara cadangan offline yang dipakai tiap bahasa |
| `waifu avatar` / `waifu avatar --demo` | Tampilkan avatar saja, tanpa mendengarkan; `--demo` mengganti ekspresi setiap 3 detik, dan reaksi elusan atau colekan dicetak di terminal (lihat [Avatar 3D](#avatar-3d-つくよみちゃん) dan [Avatar Live2D & interaksi](#avatar-live2d--interaksi)) |
| `waifu lafal list` / `add <kata> <bacaan>` / `remove <kata>` | Lihat, tambah, dan hapus cara baca kata untuk suara Indonesianya (lihat [Kamus Cyrene](#kamus-cyrene)) |
| `waifu memory list` / `add` / `forget` / `clear` | Lihat, tambah, dan hapus catatan ingatannya (lihat [Ingatan Cyrene](#ingatan-cyrene)) |
| `waifu schedule list` / `cancel <id>` / `clear` | Lihat dan batalkan pengingat dan perintah terjadwal (lihat [Jadwal, pengingat, dan rutinitas](#jadwal-pengingat-dan-rutinitas)) |
| `waifu routine list` / `run <nama>` | Lihat rutinitas dari `config.yaml`, atau jalankan satu sekarang (`--dry-run`: perangkat simulasi) |
| `waifu todo` / `todo add <teks> [--due YYYY-MM-DD]` / `done <id>` / `remove <id>` / `list --done` | Lihat, tambah, selesaikan, dan hapus tugas di daftar tugas (lihat [Daftar tugas](#daftar-tugas)) |
| `waifu briefing` | Tampilkan ringkasan hari ini (tanggal, cuaca, jadwal) tanpa otak, untuk mengecek cuaca (lihat [Sapaan pagi dan ringkasan harian](#sapaan-pagi-dan-ringkasan-harian)) |
| `waifu rain` | Tampilkan kapan hujan berikutnya dalam 24 jam di kota `briefing.city`, dibaca seperti peringatan hujannya, tanpa otak (lihat [Peringatan hujan](#peringatan-hujan)) |
| `waifu cuaca` / `cuaca besok` / `akhir-pekan` / `YYYY-MM-DD` | Prakiraan cuaca sisa hari ini, besok, akhir pekan, atau tanggal lain sampai enam hari lagi di kota `briefing.city`, per bagian hari, tanpa otak (lihat [Cuaca beberapa hari](#cuaca-beberapa-hari)) |
| `waifu usage` / `waifu usage --month 2026-09` | Perkiraan biaya API Claude per hari bulan ini atau bulan lain (lihat [Biaya API Claude](#biaya-api-claude)) |
| `waifu phone setup` / `pair` / `devices` / `forget <id>` | Siapkan Cyrene di HP lewat Tailscale, pasangkan HP dengan kode, lihat dan lupakan HP (lihat [Cyrene di HP](#cyrene-di-hp)) |
| `waifu pc` | Tampilkan kondisi PC saat ini: beban CPU, RAM, sisa disk, GPU NVIDIA, dan waktu sejak Windows terakhir di-restart (lihat [Kesehatan PC](#kesehatan-pc)) |
| `waifu playtime` / `playtime yesterday` / `week` / `month` | Berapa lama HSR, Genshin, dan ZZZ terbuka hari ini, kemarin, minggu ini, atau bulan ini, dari catatan Cyrene selama `waifu run` menyala (lihat [Jam main](#jam-main)) |
| `waifu pengingat-game` / `pengingat-game hapus <id>` | Lihat dan hapus pengingat yang dia ucapkan saat game dibuka, misalnya "pas buka HSR nanti, ingetin aku farming relic" (lihat [Pengingat saat game dibuka](#pengingat-saat-game-dibuka)) |
| `waifu internet` | Cek internet sekarang (router, internet, DNS, server HoYoLAB/HoYoverse) dan kapan saja putus hari ini, dari catatan Cyrene selama `waifu run` menyala (lihat [Pemantau internet](#pemantau-internet)) |
| `waifu focus` | Waktu fokus hari ini dan minggu ini dari catatan teman fokus (pomodoro) Cyrene selama `waifu run` menyala (lihat [Teman fokus](#teman-fokus)) |
| `waifu jurnal` / `jurnal show <tanggal>` / `add <teks> [--date YYYY-MM-DD]` / `forget <tanggal> [--yes]` | Lihat hari yang ada catatannya, catatan satu hari, tambah catatan, dan hapus catatan satu hari di jurnal harian (lihat [Jurnal harian](#jurnal-harian)) |
| `waifu rekap` / `rekap kemarin` / `rekap YYYY-MM-DD` | Rekap hari ini sampai sekarang, kemarin, atau tanggal lain sampai 30 hari ke belakang dari catatan di PC ini (tugas, fokus, jam main, pull, kata Jepang, jumlah catatan jurnal, jadwal, internet putus), tanpa otak (lihat [Rekap hari ini](#rekap-hari-ini)) |
| `waifu jepang` / `jepang progres` | Kata Jepang hari ini dengan contoh kalimatnya, atau berapa kata yang sudah dipelajari, dikuasai, dan perlu diulang hari ini, tanpa mencatat apa pun (lihat [Belajar bahasa Jepang](#belajar-bahasa-jepang)) |
| `waifu youtube <link atau judul lagu>` | Buka video atau playlist YouTube itu, atau halaman pencarian YouTube untuk judulnya, di browser default (lihat [Lagu di YouTube](#lagu-di-youtube)) |
| `waifu offers` / `offers games` / `offers ai` | Game gratis dan promo AI yang tersimpan, dengan linknya; `waifu offers ai --scan` mencari promo AI sekarang dengan Claude (berbayar) (lihat [Pencarian web dan promo gratis](#pencarian-web-dan-promo-gratis)) |
| `waifu tepira` / `tepira dompet` / `tujuan [nama]` / `transaksi [-n N]` / `token` | Keuangan dari tepira: ringkasan bulan ini, saldo dompet, tujuan tabungan, dan transaksi terbaru; `token` membuat token API Cyrene baru tanpa internet (lihat [Keuangan dari tepira](#keuangan-dari-tepira)) |
| `waifu sholat` / `sholat YYYY-MM-DD` | Waktu sholat hari ini atau tanggal lain untuk tempat Master, dihitung tanpa internet dengan metode Kemenag RI, juga saat fiturnya mati (lihat [Waktu sholat](#waktu-sholat)) |
| `waifu tombol` | Buka tombol cepat (lampu, AC, colokan, rutinitas) di browser default PC ini, tanpa otak; tutup dengan Ctrl+C (lihat [Tombol cepat di PC](#tombol-cepat-di-pc)) |
| `waifu check` | Periksa config, versi `waifu run` yang berjalan, kabar versi baru, kode IR, aplikasi PC, rutinitas, tombol cepat di PC, voiceprint, otak, ingatan, jadwal, daftar tugas, jurnal, belajar bahasa Jepang, lagu YouTube, sapaan pagi, peringatan hujan, biaya API, kesehatan PC, pemantau internet, jam main, pengingat saat game dibuka, teman fokus, mode game, mode tamu, volume suaranya, waktu sholat, pencarian web, promo gratis, paket suara, CUDA, mic, suara cadangan offline, suara anime, avatar, keuangan tepira, dan Cyrene di HP |

Opsi untuk semua perintah: `--config <file>` (default `config.yaml`), `--dry-run`
(perangkat simulasi), `-v` (log lebih rinci), `--log-file <file>` (log juga ditulis
ke file UTF-8; diganti file baru setiap 1 MB, 3 file lama disimpan sebagai `.1`–`.3`).

Hanya satu `waifu run` yang bisa berjalan sekaligus (kunci di `data\run.lock`). Yang
kedua langsung berhenti dengan pesan "Cyrene sudah berjalan (mungkin dari autostart)".

Contoh kalimat saat `waifu run`:

- "Cyrene, nyalakan lampu kamar." / "Cyrene, redupkan lampu jadi tiga puluh persen."
- "Cyrene, AC dua puluh empat derajat dan matikan proyektor."
- "Matikan TV, Cyrene." (nama boleh di mana saja dalam kalimat)
- "Cyrene, pause musiknya." / "Cyrene, buka Spotify." (lihat
  [Musik, volume, dan aplikasi PC](#musik-volume-dan-aplikasi-pc))
- "Cyrene, kecilkan suaramu." (lihat [Volume suaraku](#volume-suaraku))
- "Cyrene, putar lagu Hope Is the Thing With Feathers." (lihat
  [Lagu di YouTube](#lagu-di-youtube))
- "Cyrene?" saja: dia menjawab "はい、Master?" dan menunggu perintah berikutnya.
- Setelah dia menjawab, selama 8 detik (`assistant.followup_seconds`) kamu bisa
  lanjut tanpa memanggil namanya: "terus nyalakan TV juga".
- "Ulangi" atau "tadi bilang apa?" dalam 8 detik itu: dia mengucapkan kalimatnya lagi,
  seketika (lihat [Ulangi](#ulangi)).
- "Cyrene, tadi kamu denger apa?" saat dia tidak menjawab: dia bilang yang dia dengar dan
  kenapa diam (lihat [Diagnosa suara](#diagnosa-suara)).
- "Cyrene, internetnya kenapa?" atau "kok lemot?": dia mengecek saat itu dan menjawab sendiri,
  juga saat internet putus (lihat [Pemantau internet](#pemantau-internet)).
- Ctrl+Alt+C, juga di tengah game layar penuh: dia langsung mendengarkan tanpa dipanggil
  namanya; Ctrl+Alt+Q: dia berhenti mendengarkan sampai ditekan lagi (lihat
  [Hotkey global](#hotkey-global)).
- "Cyrene, cara baca Acheron itu A Ke Ron.", lalu "iya" saat dia bertanya: mulai kalimat
  berikutnya dia membacanya begitu; "Cyrene, lupakan cara baca Acheron" menghapusnya (lihat
  [Kamus Cyrene](#kamus-cyrene)).

Obrolan dilupakan setelah 10 menit tanpa percakapan atau 40 giliran
(`assistant.session_idle_minutes`, `assistant.max_session_turns`). Hal penting tentang
Master tetap diingat lewat catatannya (lihat [Ingatan Cyrene](#ingatan-cyrene)).

## Respons instan

Jawaban dari Claude butuh 2 sampai 5 detik, lebih lama kalau dia memakai tool, dan selama itu
dia diam: kamu tidak tahu apakah dia mendengar. Karena itu, kalau otaknya belum menjawab setelah
1,2 detik, Cyrene bilang satu kalimat pendek dulu dengan suaranya sendiri, lalu jawabannya
menyusul begitu siap:

> "Cyrene, besok hujan nggak?"
>
> "Sebentar, kucek dulu ya."
>
> "Besok pagi cerah, Master, tapi sore ada hujan ringan."

Kalimat pendeknya dipilih acak dari enam: "Hmm, bentar ya…", "Sebentar, kucek dulu ya.", "Oke,
kupikirkan dulu…", "Iya, aku dengar kok, Master…" (dengan panggilanmu dari
`assistant.owner_call`), "Hmm, coba kulihat dulu ya.", dan "Aku cari tahu dulu ya.", tidak
pernah sama dengan salah satu dari dua kalimat sebelumnya. Jawaban yang siap dalam 1,2 detik
datang tanpa kalimat itu, seperti biasa.

- Hitungan 1,2 detik dimulai saat dia mulai berpikir, bukan saat kamu selesai bicara: sebelum
  itu dia menunggu jeda 0,7 detik untuk tahu kalimatmu selesai (`audio.vad.silence_ms`), lalu
  Whisper menulisnya. Jadi dari selesai bicara, kalimat pendek itu datang kira-kira 2 sampai 3
  detik kemudian.
- Dengan Claude, yang butuh 2 sampai 5 detik, hampir setiap kalimatmu yang dijawab otak
  mendapatkannya, juga "makasih" atau "nyalakan lampu". Kalau terasa terlalu sering, naikkan
  `nod.after_ms` (misalnya 2500): hanya jawaban yang lebih lambat dari itu yang mendapatkannya.
- Kalimat-kalimat itu disiapkan sekali di latar belakang setelah `waifu run` siap (sesudah
  "Cyrene siap, Master." kalau dengan `--announce`) dan disimpan di memori, jadi tidak pernah
  menunggu edge-tts atau suara anime. Satu-dua pertanyaan pertama tepat setelah mulai mungkin
  belum mendapatkannya.
- Kalimat yang saat disiapkan keluar dengan suara cadangan Windows (edge-tts gagal, misalnya
  internet belum tersambung saat login) atau tanpa suara anime (RVC gagal) tidak dipakai, supaya
  tidak ada "Hmm" dengan suara lain sebelum jawaban dengan suara anime. Yang belum siap dicoba
  lagi di latar belakang sekitar 10 menit kemudian (sedikit lewat `tts.fallback.retry_s`), terus
  sampai keenamnya siap; sementara itu dia diam menunggu jawaban seperti sebelum v1.52.
- Hanya untuk kalimatmu sendiri yang dijawab otak. Tidak untuk yang dijawab seketika tanpa otak
  ("Cyrene?", "ulangi", "tadi kamu denger apa?", "cara baca ...", "aku sudah bangun"), sapaan
  pagi, pertanyaan jurnal, laporan game mingguan, pesan dari HP, atau `waifu chat`. Selama teman
  fokus atau mode game menahan pengumuman, pertanyaanmu tetap mendapatkannya: kamu sedang bicara
  dengannya.
- Avatar bergerak seperti bicara, lalu kembali berpose berpikir sampai jawabannya datang. Kalimat
  itu tidak membuka jendela 8 detik dan tidak diulang oleh "ulangi" (yang diulang jawabannya).
  Volume suaranya dan mode bisik juga berlaku untuknya.
- Klik dua kali atau Ctrl+Alt+X di tengah kalimat itu hanya memotong kalimat itu: dia tetap
  berpikir (avatar tetap berpose berpikir) dan jawabannya tetap datang. Selama itu dia belum
  mendengar, jadi tunggu jawabannya dulu; setelah jawabannya, jendela 8 detik terbuka seperti
  biasa.

Sudah menyala tanpa ditulis. Untuk mengubahnya, tulis di `config.yaml` (berlaku setelah
`waifu run` dijalankan ulang):

```yaml
nod:
  enabled: true      # false: dia diam sampai jawabannya siap, seperti sebelum v1.52
  after_ms: 1200     # setelah N milidetik berpikir tanpa jawaban (600-5000)
```

Tes otomatis tidak bisa mendengar bagaimana suara Indonesia edge-tts mengucapkan "Hmm". Coba
sendiri dengan `waifu say "Hmm, bentar ya…"` (butuh internet dan speaker); kalau terdengar aneh,
`nod.enabled: false` mematikannya.

## Kartu teks

Isi yang panjang (daftar lebih dari tiga hal, jadwal, tabel, tautan, kode, langkah-langkah,
terjemahan yang lebih dari satu kalimat) sulit diikuti kalau dibacakan semuanya. Karena itu
Cyrene hanya mengucapkan intinya dalam satu-dua kalimat, lalu menampilkan sisanya sebagai kartu
teks:

> "Cyrene, cuaca seminggu ke depan gimana?"
>
> "Ini ramalan seminggu, kutampilkan di layar ya. Intinya hujan Rabu dan Kamis."

dan di bawah avatarnya muncul kartu "Cuaca 7 hari" dengan satu baris per hari. Otaknya menulis
intinya dan kartunya dalam satu jawaban, jadi tidak ada panggilan API tambahan; dengan Claude
maupun Ollama.

- **Di PC:** kartu tampil di bagian bawah jendela avatar (maskot Electron maupun jendela Edge),
  di atas baris kredit. Isinya bisa digulir dengan mouse dan dipilih untuk disalin. Kartu hilang
  sendiri setelah 2 menit (`card.show_seconds`), saat × diklik, atau saat kartu berikutnya
  datang. Selama kursor ada di atas kartu, waktunya berhenti; setelah kursor pergi, kartu masih
  tampil sisa waktunya, paling sedikit 10 detik. Kartu tidak pernah mengambil fokus dari
  aplikasi yang sedang kamu pakai, dan klik di luar kartu tetap tembus ke desktop seperti biasa.
- **Tautan:** tautan http atau https di kartu bergaris bawah; klik untuk membukanya di browser
  default. Tautan tidak pernah terbuka sendiri, dan hanya tautan yang tertulis di kartu yang
  sedang tampil yang bisa dibuka.
- **Di HP:** kalau kamu bertanya lewat HP, gelembung jawabannya berisi intinya lalu kartunya
  (judul, daftar, dan tautan yang bisa diketuk), dan pesan suaranya hanya intinya. Pertanyaan
  lewat suara di PC tidak mengirim apa pun ke HP.
- **`waifu chat`:** intinya dicetak seperti biasa, lalu kartunya menjorok di bawahnya; dengan
  `--speak` hanya intinya yang diucapkan.
- **"Ulangi"** mengulang intinya saja. **"Tampilkan lagi kartunya"**, "kartu tadi", "mana
  kartunya?", "tunjukin kartunya lagi", "tampilkan lagi kartu terakhir", atau "tampilkan
  kartunya sekali lagi" menampilkan kartu terakhir sekali lagi, seketika
  dan tanpa bertanya ke otaknya: "Ini kartunya lagi, Master." Kalau belum ada kartu: "Belum ada
  kartu yang bisa kutampilkan lagi, Master." Kalimat lain yang menyebut kartu ("kartu tadi
  isinya apa?", "kartu yang mana?") tetap dijawab otaknya.
- Kalau jawabannya hanya kartu, dia bilang "Kutampilkan di layar ya, Master."
- Laporan game mingguan boleh menaruh rinciannya di kartu; notifikasinya ke HP berisi intinya
  lalu isi kartunya sebagai teks biasa. Sapaan pagi dan pertanyaan jurnal tetap diucapkan, tanpa
  kartu.
- **Mode tamu:** kartu tetap berjalan seperti biasa. Giliran yang menyalakan atau mematikan mode
  tamu tidak menampilkan kartu, dan kartu yang sedang tampil langsung disembunyikan; kartu dari
  sebelum mode tamu berganti tidak bisa ditampilkan lagi.

Sudah menyala tanpa ditulis. Untuk mengubah lamanya, tulis di `config.yaml` (berlaku setelah
`waifu run` dijalankan ulang):

```yaml
card:
  show_seconds: 120   # berapa detik kartu tampil di avatar (0-3600; 0 = sampai ditutup)
```

Batasannya:

- Dengan `avatar.enabled: false`, dia tetap bilang "kutampilkan di layar" tetapi di PC tidak
  ada yang tampil, dan "tampilkan lagi kartunya" dijawab bahwa avatarnya sedang tidak tampil.
  Dengan `avatar.window: none`, kartunya tampil di tab browser yang membuka halaman avatar
  (alamatnya ada di `waifu check`).
- Kalau halaman avatar sedang tidak terbuka di mana pun (maskotnya sudah ditutup, atau dengan
  `window: none` tidak ada tab yang membukanya), dia tetap bilang "kutampilkan di layar" dan
  "Ini kartunya lagi, Master." walaupun tidak ada yang tampil. Lewat HP dan `waifu chat`
  kartunya tetap tampil.
- Kartu yang ditutup dengan × muncul lagi kalau halaman avatar dimuat ulang (misalnya sesudah
  GPU reset saat game fullscreen) selagi waktunya belum habis.
- Tautan hanya dikenali kalau ditulis lengkap dengan `http://` atau `https://`.
- Ollama (`qwen3:8b`) kadang menulis tanda kartunya kurang rapi. Tanda yang dikenali
  (`[[kartu: ...]]` dengan berbagai variasinya, `[kartu: ...]`, atau `[kartu ...]` sebaris
  sendiri) dan isi kartunya tidak pernah dibacakan. Tanda yang tidak dikenali sama sekali,
  misalnya baris "Kartu: Cuaca" tanpa kurung siku, tidak membuat kartu: seluruh jawabannya
  dibacakan seperti sebelum ada kartu teks.

## Jalan otomatis saat login

Supaya Cyrene langsung siap setelah PC menyala dan kamu login Windows.

**Syarat:** `config.yaml` sudah benar (`waifu check`) dan suara Master sudah
didaftarkan dengan `waifu enroll`. Tanpa itu `enable` menolak; `--force` tetap
mengaktifkannya.

Jalankan dari folder repo:

```powershell
waifu autostart enable     # jeda 15 detik setelah login; ubah dengan --delay 30
waifu autostart status     # aktif atau tidak, sedang berjalan atau tidak, 5 baris log terakhir
waifu autostart disable    # matikan lagi
```

Setiap kali login, Windows menjalankan perintah yang tersimpan di
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (nama `Cyrene`, tanpa hak admin):

1. Jendela **"Cyrene"** muncul dalam keadaan minimize di taskbar.
2. Setelah 15 detik, model Whisper dan verifikasi suara dimuat.
3. Dia bilang **"Cyrene siap, Master."** lalu mendengarkan seperti `waifu run` biasa:
   panggil namanya dulu. Pengumuman ini bukan jawaban, jadi tidak membuka jendela
   8 detik tanpa nama.

Autostart selalu memakai `config.yaml` di folder repo dan perangkat asli;
`--config` dan `--dry-run` tidak berpengaruh pada perintah yang didaftarkan.

- **Menghentikan:** tutup jendela konsol "Cyrene" (yang minimize di taskbar, ikon
  Command Prompt). Dia jalan lagi pada login berikutnya. Maskot avatar adalah jendela
  lain bernama "Avatar Cyrene"; menutupnya **tidak** menghentikan Cyrene.
- **Mematikan autostart:** `waifu autostart disable`. Di Task Manager → Startup apps
  (dan Settings → Apps → Startup) entrinya **tidak** bernama "Cyrene", tetapi
  "Windows Command Processor" (penerbit Microsoft Corporation), karena yang dijalankan
  saat login adalah `cmd.exe`. Jangan matikan dari sana: Windows menyimpan tanda
  "Disabled" itu terpisah, `waifu autostart disable` lalu `enable` tidak
  menghapusnya, dan `enable` maupun `status` tidak bisa melihatnya, jadi keduanya
  tetap bilang aktif padahal Windows melewatinya setiap login. Kalau sudah terlanjur
  dimatikan dari sana, nyalakan lagi juga dari sana.
- **Log:** `logs\waifu.log` di folder repo (berganti file baru setiap 1 MB). Kalau
  jendela "Cyrene" muncul lalu langsung hilang (misalnya mic USB atau Bluetooth
  belum siap saat login), penyebabnya tercatat di sana sebagai baris `ERROR`; lihat
  dengan `waifu autostart status`, yang juga bisa dijalankan dari folder lain. Log ini
  berisi transkrip ucapan Master (lihat [Privasi](#privasi)).
- **Hanya satu Cyrene:** selama jendela itu terbuka, `waifu run`, `waifu enroll`, dan
  `waifu speaker-test` di terminal lain menolak jalan ("Cyrene sudah berjalan").
  Tutup jendela "Cyrene" dulu. Tanpa penolakan ini dia akan ikut mendengar kalimat
  pendaftaran (beberapa diawali namanya) dan menjalankannya, dan suaranya bisa ikut
  terekam ke voiceprint baru.
- **Folder repo atau venv dipindah:** perintah yang tersimpan masih menunjuk ke path
  lama, dan `status` menandainya dengan `!`. Jalankan lagi `waifu autostart enable`
  dari folder yang baru.

## Versi baru

`waifu run` menjalankan kode yang ada saat dia dimulai. Kalau jendelanya dibiarkan terbuka
berhari-hari (misalnya dari autostart), versi baru yang sudah ada di folder proyek baru aktif
setelah dia dimulai ulang. Sejak v1.26 Cyrene memberi tahu sendiri:

```text
(09.30)  Cyrene : "Sayang, ada versi baru Cyrene: v1.27, kamera HP, dan satu versi lain. Mulai ulang aku kalau sempat ya."
```

Kalimat yang sama juga dikirim ke HP kalau [notifikasi](#3-notifikasi-ke-hp-ntfy) menyala.
Fitur ini sudah menyala dari awal (`version_notice.enabled: true`).

**Sekali mulai ulang manual dulu.** `waifu run` yang dimulai sebelum v1.26 tidak mencatat
versinya, jadi dia belum bisa memberi tahu apa-apa. Tutup jendela "Cyrene" (atau Ctrl+C), lalu
jalankan lagi `waifu run` (atau logout lalu login kalau pakai autostart) sekali saja; sesudah itu
dia memberi tahu sendiri.

### Kapan dia memberi tahu

- **Saat CHANGELOG punya versi baru.** Setiap 30 menit dia membandingkan `CHANGELOG.md` dan
  `pyproject.toml` di folder proyek dengan yang ada saat dia dimulai. Versi baru adalah judul
  `## v1.26: ...` yang lebih tinggi dari versi yang sedang berjalan. Perbaikan kecil tanpa
  versi baru di CHANGELOG tidak diberitahukan; `waifu check` menyebutnya (lihat di bawah).
- **Sekali untuk setiap versi.** Kalau sesudah itu masuk versi yang lebih baru lagi, dia memberi
  tahu sekali lagi, dengan hitungannya sendiri. Judul versi yang diganti tidak dihitung sebagai
  versi baru.
- **Hanya di jam aktif**, di antara `active_from` dan `active_until` (bawaan 08.00-22.00, jam
  PC). Di luar jam itu dia tidak memeriksa apa pun; versi yang masuk malam hari diberitahukan
  pagi berikutnya, begitu jam aktif mulai.
- **Tidak pernah memotong.** Seperti peringatan hujan, kalimatnya diucapkan di sela obrolan:
  tidak saat dia menjawab, tidak saat Master bicara, dan tidak selama jendela lanjutan sesudah
  jawabannya masih terbuka. Notifikasi HP-nya langsung dikirim.
- **Kalau filenya tidak terbaca** (misalnya `CHANGELOG.md` rusak), dia diam; log mencatatnya
  sekali (`Could not read the code on disk`), dan dia mencoba lagi di pemeriksaan berikutnya.
  Dia baru memberi tahu sesudah `CHANGELOG.md` dan `pyproject.toml` terbaca utuh, supaya
  langkah `pip install` tidak tertinggal.
- **Dia tidak pernah memulai ulang dirinya sendiri.** Memulai ulang berarti membuka jendela
  konsol baru, yang bisa muncul di atas game layar penuh, dan proses barunya akan berebut kunci
  `data\run.lock` dengan yang lama. Kapan mulai ulang, kamu yang menentukan.

### Kalau paket Python-nya berubah

Kadang versi baru butuh paket Python lain (daftar `dependencies` di `pyproject.toml` berubah).
Dia lalu bilang "... Paket Python-nya juga berubah. Tutup aku dulu, jalankan pip install, lalu
mulai ulang aku ya; perintahnya ada di HP." dan notifikasi HP-nya berisi perintahnya:

```powershell
# Tutup jendela "Cyrene" (atau Ctrl+C) dulu: selama dia jalan, Windows mengunci waifu.exe.
cd <folder proyek>
.venv\Scripts\python -m pip install -e .
waifu run
```

Paket yang berubah tanpa versi baru di CHANGELOG tidak diberitahukan sendiri: biasanya versinya
masih dikerjakan. Dia menyebutnya bersama versi baru berikutnya, dan `waifu check` sudah
menampilkannya.

Tanpa notifikasi HP dia bilang "perintahnya ada di waifu version": `waifu version` dan
`waifu check` menampilkan perintah yang sama, juga sesudah dia ditutup. Jalankan perintahnya
sebelum memulai `waifu run` lagi: `waifu run` yang baru mencatat paket yang baru, jadi
pengingatnya hilang meskipun `pip install` belum dijalankan. Paket suara (`.[voice]`) tidak
ikut dihitung; pasang ulang lewat langkah di [Paket suara](#2-paket-suara-stt-verifikasi-suara)
kalau CHANGELOG memintanya.

### Mengecek: `waifu version` dan `waifu check`

`waifu version` (jalan juga tanpa `config.yaml`, dari folder mana pun) menampilkan versi di disk
dan versi `waifu run` yang sedang berjalan:

```text
Kode di disk: v1.26, versi baru (commit 3f9c2ab)
Yang berjalan: v1.25, peringatan hujan (commit ec4aadf), dimulai 2026-10-13 08:15
! waifu run yang sedang jalan masih v1.25 (dimulai 2026-10-13); mulai ulang supaya v1.26 aktif
```

Baris terakhirnya juga muncul di `waifu check`, tepat di bawah "Konfigurasi valid":

| Baris | Artinya |
|---|---|
| `✓ waifu run: v1.26, terbaru` | yang berjalan sudah versi terbaru |
| `✓ waifu run: v1.26, terbaru; ada perubahan kecil sejak dimulai, mulai ulang kalau sempat` | ada commit baru tanpa versi baru, misalnya perbaikan |
| `! waifu run yang sedang jalan masih v1.25 (dimulai 2026-10-13); mulai ulang supaya v1.26 aktif` | ada versi baru di disk |
| `! ... dan paket Python-nya berubah: tutup Cyrene, jalankan .venv\Scripts\python -m pip install -e . di folder proyek, lalu mulai ulang ...` | pasang paketnya dulu (lihat di atas) |
| `! waifu run yang sedang jalan versinya tidak tercatat (lebih lama dari v1.26); mulai ulang ...` | `waifu run` dari sebelum v1.26, atau `waifu enroll`/`speaker-test` sedang jalan |
| `✓ waifu run tidak berjalan; versi di disk v1.26, versi baru` | tidak ada `waifu run` yang jalan |
| `! waifu run tidak berjalan; versi di disk v1.26, versi baru; paket Python-nya berubah sejak terakhir dijalankan: kalau belum, jalankan .venv\Scripts\python -m pip install -e . di folder proyek sebelum waifu run` | dia sudah ditutup, tapi paket barunya belum tentu terpasang |

Di bawahnya ada `✓ Kabar versi baru: dicek tiap 30 menit, jam 08:00-22:00` (atau
`! Kabar versi baru mati`).

Setiap `waifu run` dimulai, versinya dicatat di `data\running.json`: versi dan judul dari
CHANGELOG, hash daftar paket, commit git (tanpa git: waktu file terbaru di `src\waifu`), waktu
mulai, dan nomor prosesnya. Apakah dia masih berjalan dilihat dari kunci `data\run.lock` dan
nomor proses itu, bukan dari ada tidaknya file ini: menutup jendela konsol tidak sempat
menghapusnya.

### Pengaturan

```yaml
version_notice:
  enabled: true
  check_minutes: 30      # bandingkan dengan kode di disk setiap sekian menit (5-1440)
  active_from: "08:00"   # hanya mulai jam ini (jam PC)
  active_until: "22:00"  # dan sebelum jam ini
  push: true             # kirim kabarnya juga ke HP (kalau notify menyala)
```

Tulis jamnya dengan tanda kutip, dan `active_from` harus sebelum `active_until`. Untuk
mematikannya, `enabled: false` (`waifu run` tetap mencatat versinya untuk `waifu check` dan
`waifu version`); untuk tanpa notifikasi HP, `push: false`. Jalankan ulang `waifu run` setelah
mengubahnya.

## Suara anime (つくよみちゃん)

Dengan `tts.rvc.enabled: true`, **semua** jawaban Cyrene, bagian Indonesia maupun
Jepang, tetap dibuat dulu oleh edge-tts, lalu seluruh klipnya diubah ke satu suara
karakter, つくよみちゃん, oleh model RVC (voice conversion). Modelnya kamu latih
sendiri di PC ini dari korpus gratis Tsukuyomi-chan (sekitar 11 menit suara).

- Konversi dijalankan **Applio** (lisensi MIT) di server lokal `tools/rvc_server.py`,
  yang memuat model sekali lalu melayani di `127.0.0.1:9881` (hanya dari PC ini).
  Applio memakai Python 3.12 di env-nya sendiri, terpisah dari `.venv` Cyrene.
- `waifu run`, `waifu say`, dan `waifu chat --speak` menyalakan server itu sendiri
  (`run` melakukannya saat memuat model, sebelum bilang siap) dan mematikannya saat
  selesai, juga saat jendelanya ditutup: Windows mematikan server bersama perintah
  yang menyalakannya. Server yang sudah menjawab di port itu sebelumnya (misalnya
  milik Cyrene autostart) dipakai ulang dan dibiarkan menyala, asal modelnya sama
  dengan `tts.rvc.model`. Kalau dua perintah menyala bersamaan, yang kedua menunggu
  server dari yang pertama, jadi model tidak dimuat dua kali ke GPU.
- **Dia tidak pernah diam karena RVC.** Kalau server tidak bisa jalan, muncul
  `! Suara anime tidak tersedia: ... Cyrene memakai suara biasa.` dan dia bicara
  dengan suara edge-tts biasa. Kalau konversi gagal di tengah jalan, jawaban itu
  diputar dengan suara biasa dan log mencatat peringatannya sekali. Kalau servernya
  tidak bisa dihubungi sama sekali, jawaban selama 1 menit berikutnya langsung memakai
  suara biasa (tanpa menunggu server), lalu dia mencoba lagi.
- **Biaya:** perkiraan tambahan sekitar 0,3–1 detik per jawaban (belum diukur di PC
  ini; bandingkan sendiri dengan `waifu say` dan `waifu say --raw`), plus VRAM untuk
  model RVC selama server menyala.

### Hak suara (baca dulu)

- Korpus つくよみちゃん gratis. Menurut ketentuannya, pemakaian pribadi boleh tanpa
  batasan selama suara itu, dan audio apa pun yang dibuat darinya, tidak
  dipublikasikan.
- **Sebelum mempublikasikan audio apa pun** dengan suara ini (video, stream, media
  sosial), baca ketentuan terbaru di <https://tyc.rei-yumesaki.net/material/corpus/>
  dan cantumkan kredit **つくよみちゃんコーパス（CV.夢前黎）**.
- **Jangan pernah menyebarkan ulang** korpus maupun model hasil training (`.pth`,
  `.index`). Korpus, model, dan audio hasil konversi tinggal di `C:\AI`, di luar repo
  ini, dan tidak pernah di-commit.

### 1. Pasang Applio (sekali)

Butuh [uv](https://docs.astral.sh/uv/getting-started/installation/) dan git. Unduhannya
beberapa GB (torch CUDA dan model pendukung).

```powershell
git clone https://github.com/IAHispano/Applio C:\AI\Applio
Set-Location C:\AI\Applio
uv venv env --python 3.12
uv pip install --python env\Scripts\python.exe -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match
env\Scripts\python.exe core.py prerequisites
```

`prerequisites` mengunduh model pendukung (pretrained HiFi-GAN, `rmvpe`, `contentvec`)
dan ffmpeg ke dalam folder Applio. Index `cu128` dibutuhkan untuk RTX seri 50, sama
seperti paket suara Cyrene.

### 2. Unduh korpus

Unduh korpus dari <https://tyc.rei-yumesaki.net/material/corpus/> (baca ketentuannya
di halaman yang sama), ekstrak ke `C:\AI\voices\`, lalu kumpulkan file WAV-nya di satu
folder dataset, misalnya `C:\AI\voices\tsukuyomi_dataset`.

### 3. Latih model

Selama training GPU dipakai penuh; jangan jalankan Cyrene bersamaan. Dari
`C:\AI\Applio`:

```powershell
env\Scripts\python.exe core.py preprocess --model-name tsukuyomi --dataset-path C:\AI\voices\tsukuyomi_dataset --sample-rate 40000
env\Scripts\python.exe core.py extract --model-name tsukuyomi --f0-method rmvpe --embedder-model contentvec --sample-rate 40000 --gpu 0
env\Scripts\python.exe core.py train --model-name tsukuyomi --vocoder HiFi-GAN --sample-rate 40000 --batch-size 8 --total-epoch 250 --save-every-epoch 50 --gpu 0
env\Scripts\python.exe core.py index --model-name tsukuyomi
```

- `--sample-rate 40000`, `rmvpe`, `contentvec`, dan `HiFi-GAN` harus sama di semua
  langkah: server Cyrene juga mengonversi dengan `rmvpe` dan `contentvec`.
- `extract` tanpa `--gpu 0` berjalan di CPU (jauh lebih lambat).
- `--total-epoch` adalah batas atas; `--save-every-epoch 50` menyimpan checkpoint
  setiap 50 epoch, jadi kamu bisa berhenti lebih awal (Ctrl+C) kalau checkpoint yang
  ada sudah bagus.
- Di PC ini (RTX 5060 Ti, korpus 100 kalimat ≈ 11 menit) satu epoch ±20 detik, jadi
  250 epoch ±70 menit. Hasil pengukuran (kemiripan ECAPA ke suara つくよみちゃん /
  error transkripsi Whisper untuk kalimat Indonesia; edge-tts asli: 0,43 / 7,7%):
  epoch 50 → 0,73 / 10,4%, epoch 100 → 0,75 / 7,7%, **epoch 150 → 0,75 / 7,7%**,
  epoch 200 → 0,76 / 14,8%, epoch 250 → 0,77 / 10,9%. Mulai epoch 200 bahasa
  Indonesianya terdengar berlogat Jepang (korpusnya berbahasa Jepang), jadi yang
  dipakai adalah **epoch 150**, disalin ke `C:\AI\voices\models\tsukuyomi_150e.pth`.
- `train` membuat `C:\AI\Applio\logs\tsukuyomi\tsukuyomi.index` di akhir, dan `index`
  membuatnya kalau belum ada (misalnya setelah training dihentikan dengan Ctrl+C).
  Keduanya **melewati** index yang sudah ada, walau tetap menulis "generated
  successfully". Setelah menambah atau mengganti audio di dataset, hapus dulu
  `tsukuyomi.index` itu sebelum `index`, supaya index dibuat ulang dari fitur yang baru.

### 4. Aktifkan di config.yaml

Hasil training ada di `C:\AI\Applio\logs\tsukuyomi\`:

- `tsukuyomi_<epoch>e_<step>s.pth`: model untuk dipakai, satu per checkpoint
  (misalnya `tsukuyomi_100e_4500s.pth`). File `G_*.pth` dan `D_*.pth` hanya untuk
  melanjutkan training; jangan dipakai.
- `tsukuyomi.index`: index fitur, opsional tapi membuat warna suaranya lebih mirip.

Isi `config.yaml` dengan salah satu checkpoint dulu; yang terbaik dipilih di langkah 5.
`enabled: true` wajib: selama masih `false`, semua checkpoint terdengar sama (suara
edge-tts biasa), dan `waifu say` mengingatkan bahwa `tts.rvc.model` tidak dipakai.

```yaml
tts:
  rvc:
    enabled: true
    applio_dir: C:/AI/Applio
    model: C:/AI/Applio/logs/tsukuyomi/tsukuyomi_100e_4500s.pth   # checkpoint yang dicoba
    index: C:/AI/Applio/logs/tsukuyomi/tsukuyomi.index
    pitch: 4            # sudah diukur: edge-tts sekitar 275 Hz, korpus sekitar 351 Hz
    index_rate: 0.75
    protect: 0.33
    port: 9881
```

Blok lengkap dengan penjelasan tiap kunci ada di `config.example.yaml`. Lalu:

```powershell
waifu check
waifu say "Halo Master, おかえりなさい! Lampu kamar sudah menyala."
waifu say --raw "Halo Master, おかえりなさい! Lampu kamar sudah menyala."
```

`waifu check` menampilkan `✓` untuk folder Applio, Python Applio, model, index,
dan `tools/rvc_server.py`; baris server menunjukkan apakah sudah ada server yang
menjawab di port itu, dengan model apa (`!` kalau belum: `run`, `say`, dan
`chat --speak` menyalakannya sendiri; `check` tidak pernah menyalakannya; `✗` kalau
server yang menjawab memakai model lain). `say --raw` memutar kalimat yang sama
dengan suara edge-tts biasa, untuk membandingkan.

### 5. Pilih checkpoint

Coba beberapa checkpoint dengan kalimat yang sama: ganti `tts.rvc.model`, lalu
`waifu say`. Setiap `waifu say` menyalakan server dengan model itu dan mematikannya
lagi, kecuali sudah ada server yang menjawab di port itu, misalnya dari Cyrene
autostart: `say` hanya memakainya kalau modelnya sama, dan kalau lain, bicara dengan
suara biasa sambil menyebut server lama yang harus dihentikan dulu. Jadi tutup dulu
jendela "Cyrene" selama membandingkan. Untuk mencoba banyak kalimat dengan satu
checkpoint, `waifu chat --speak` memuat model sekali untuk seluruh obrolan.

Epoch yang terlalu sedikit terdengar kurang mirip; yang terlalu banyak bisa mulai
serak atau berartefak. Pilih yang paling enak didengar **dan** bahasa Indonesianya
paling jelas, lalu biarkan checkpoint itu di `tts.rvc.model`.

Menyetel suara:

- **Terlalu tinggi atau rendah:** ubah `pitch` satu-dua semitone.
- **Kata Indonesia kurang jelas:** turunkan `index_rate` (misalnya `0.5`); makin
  kecil, makin sedikit warna suara korpus (dan logat Jepangnya) yang ditarik.
- **Konsonan atau napas berdesis aneh:** turunkan `protect` (misalnya `0.2`);
  `0.5` mematikan perlindungan ini.

### Pemecahan masalah suara anime

- **Pesan "Suara anime tidak tersedia: ...".** Alasannya ada di pesan itu (path yang
  tidak ditemukan, server berhenti, atau belum siap). Output server sendiri, termasuk
  error Python dari Applio, ada di `logs\rvc_server.log` di folder repo.
- **Start pertama lambat.** Server memuat model, `contentvec`, dan `rmvpe` ke GPU lalu
  melakukan pemanasan sebelum menjawab; start pertama setelah boot bisa jauh lebih
  lama dari berikutnya. Batasnya `tts.rvc.startup_timeout_s` (default 180 detik);
  naikkan kalau log menunjukkan server masih memuat saat waktunya habis.
- **Port sudah dipakai.** Kalau program lain memakai port 9881, server berhenti saat
  mulai dan lognya menyebut alamat yang sudah dipakai. Cek dengan
  `netstat -ano | findstr :9881`, lalu ganti `tts.rvc.port` (misalnya `9882`).
- **Pesan "Server suara RVC lama dengan model ... masih berjalan".** Server yang
  sudah menjawab di port itu memuat model lain dari `tts.rvc.model` (misalnya Cyrene
  autostart masih berjalan dengan checkpoint lama), dan server tidak bisa berganti
  model. Tutup jendela "Cyrene"-nya, atau hentikan server itu seperti di bawah, lalu
  coba lagi. Mengganti `tts.rvc.index` saja juga baru terpakai setelah server lama
  berhenti.
- **Menghentikan server sendiri.** Server yang dinyalakan Cyrene mati bersama perintah
  yang menyalakannya, juga saat jendela "Cyrene" ditutup. Server yang kamu jalankan
  sendiri tetap menyala sampai dihentikan: cari PID-nya dengan
  `netstat -ano | findstr :9881`, pastikan itu `python.exe` dengan
  `tasklist /FI "PID eq <pid>"`, lalu `taskkill /PID <pid> /F`.
- **Path index mengandung `trained`.** Applio mengganti `trained` dengan `added` di
  mana pun dalam path index (untuk pasangan `trained_*.index`/`added_*.index` dari RVC
  lama) dan diam-diam mengonversi tanpa index kalau file itu tidak ada. Server
  menolak index seperti itu dan berhenti saat mulai; lognya menyebut path yang dicari
  Applio. Pindahkan atau ganti nama folder atau file index supaya path-nya tidak
  mengandung `trained` (misalnya `logs\tsukuyomi_pretrained\` jadi
  `logs\tsukuyomi_base\`).
- **`tts.rvc.host`** harus alamat IPv4 loopback seperti `127.0.0.1`. Server RVC tidak
  memakai kata sandi, jadi tidak boleh terbuka ke jaringan, dan `localhost` membuat
  setiap jawaban sekitar 2 detik lebih lambat. Nilai lain ditolak dengan pesan.
- **Mematikan suara anime:** `tts.rvc.enabled: false`. Cyrene kembali ke suara
  edge-tts; Applio dan model tidak perlu dihapus.

## Suara cadangan offline

Kalau edge-tts gagal (tanpa internet, atau ditolak Microsoft seperti 403 pada 16 Oktober
2026), Cyrene tidak diam lagi: bagian jawaban itu diucapkan oleh suara bawaan Windows, yang
jalan offline di PC ini, lalu diubah ke suara つくよみちゃん kalau suara anime menyala. Teks
jawabannya tetap tampil seperti biasa.

- Suaranya dari fitur suara Windows sendiri (speech API Windows lewat Windows PowerShell
  bawaan), jadi tidak ada program atau paket Python baru. Setiap bagian butuh sekitar
  0,4 detik.
- Bagian Indonesia memakai suara Indonesia Windows ("Microsoft Andika", laki-laki), bagian
  Jepang suara Jepang (suara perempuan lebih dulu, misalnya Haruka). Bahasa yang belum punya
  suara Windows tidak punya cadangan: bagian itu dilewati seperti dulu. Suara bahasa lain
  tidak pernah dipilih sendiri, karena suara Inggris merusak ucapan Indonesia dan Jepang.
- Setelah edge-tts gagal, bahasa itu memakai suara Windows selama 10 menit
  (`tts.fallback.retry_s`) tanpa menunggu edge-tts gagal lagi di setiap jawaban (tiap
  percobaan bisa makan sampai 10 detik), lalu edge-tts dicoba lagi. Log mencatat peringatannya
  sekali per gangguan, dan sekali lagi saat edge-tts pulih.
- Dengan [pemantau internet](#pemantau-internet) menyala (bawaannya menyala), selama cek
  internetnya yang terakhir gagal, suara Windows langsung bicara tanpa mencoba edge-tts dulu, dan
  begitu cek berikutnya berhasil, edge-tts dicoba lagi di kalimat berikutnya.
- **Seperti apa suaranya:** suara pembaca Windows, jelas tapi datar dan lebih kaku dari
  edge-tts, dan Andika suara laki-laki. Dengan suara anime menyala, RVC mengubahnya ke suara
  つくよみちゃん dengan nadanya sendiri (lihat Pengaturan), jadi tetap terdengar seperti
  Cyrene, hanya kurang ekspresif. Tanpa suara anime, yang terdengar suara Windows-nya.

### 1. Tambahkan suara Indonesia dan Jepang (sekali, gratis)

Windows di PC ini baru punya suara Inggris (David, Zira, Mark), jadi suara cadangan belum
bisa dipakai sampai suaranya ditambahkan:

1. Buka **Settings → Time & language → Speech**.
2. Di bagian **Manage voices**, klik **Add voices**.
3. Centang **Indonesian (Indonesia)** dan **Japanese (Japan)**, lalu **Add**. Windows
   mengunduhnya sendiri.
4. Jalankan `waifu voices`: suara barunya muncul, misalnya
   `id-ID: Microsoft Andika (laki-laki)`.
5. Mulai ulang `waifu run` supaya dia memakai suara yang baru.

Windows 11 Home Single Language mungkin tidak menawarkan bahasa lain di daftar itu. Kalau
begitu, biarkan `id` dan `ja` di `tts.fallback.voices` kosong (`""`, bawaannya; bagian itu
tetap diam saat edge-tts gagal, seperti dulu), atau isi sementara dengan suara Inggris,
misalnya `id: Microsoft Zira`: dia tetap bicara, tapi dengan logat Inggris.

### 2. Dengarkan

```bat
waifu voices
waifu say --fallback "Halo Master, おかえりなさい! Lampu kamar sudah menyala."
waifu say --fallback --raw "Halo Master"
```

`--fallback` hanya memakai suara cadangan, lewat suara anime kalau menyala; dengan `--raw`
tanpa suara anime. `waifu check` menampilkan
`✓ Suara cadangan bahasa Indonesia: Microsoft Andika (id-ID, laki-laki)` untuk setiap bahasa
yang punya suara, atau `!` dengan bahasa yang belum punya suara dan tempat menambahkannya.

### Pengaturan suara cadangan

```yaml
tts:
  fallback:
    enabled: true
    voices:
      id: ""         # kosong = suara Indonesia yang terpasang; atau nama dari 'waifu voices'
      ja: ""         # kosong = suara Jepang yang terpasang, perempuan lebih dulu
    retry_s: 600     # detik memakai suara Windows sebelum edge-tts dicoba lagi
    rvc_pitch:
      female: 11     # semitone untuk suara anime, per jenis suara Windows
      male: 18
```

- `rvc_pitch`: berapa semitone RVC menaikkan suara Windows; edge-tts tetap memakai
  `tts.rvc.pitch`. Korpus つくよみちゃん sekitar 351 Hz; suara perempuan Windows yang diukur
  (Zira) sekitar 188 Hz (+11), suara laki-laki 95–165 Hz (+13 sampai +22). Dengan pengaturan
  bawaan, `male` dipakai Andika dan `female` dipakai suara Jepang (Haruka, Ayumi, atau
  Sayaka), dan nada keduanya belum diukur. Setel masing-masing dengan kalimat satu bahasa:
  `male` dengan `waifu say --fallback "Halo Master, lampunya sudah menyala."`, `female`
  dengan `waifu say --fallback "おかえりなさい、マスター"`. Terlalu berat, naikkan; terlalu
  cempreng, turunkan, 1–2 sekaligus, sampai bagian Indonesia dan Jepang senada.
- `enabled: false` mematikan suara cadangan: kalau edge-tts gagal, dia diam lagi seperti
  dulu (teksnya tetap tampil).

### Pemecahan masalah suara cadangan

- **`! Suara cadangan bahasa ...: belum ada suara Windows ...`.** Tambahkan suaranya
  (langkah 1), lalu mulai ulang `waifu run`.
- **`! Suara cadangan bahasa ...: '...' (tts.fallback.voices...) tidak terpasang`.** Nama di
  `tts.fallback.voices` tidak ada di `waifu voices`; salin namanya dari sana, atau kosongkan.
- **`! Suara Windows tidak terbaca (...)`.** PowerShell tidak bisa membaca daftar suara;
  alasannya ada di dalam kurung. Kebijakan Windows yang mengunci PowerShell (biasanya hanya di
  PC kantor) bisa menyebabkannya.
- **Suaranya datar terus, lebih dari 10 menit, padahal internet menyala.** edge-tts masih
  gagal, dan peringatannya hanya ditulis sekali di awal gangguan, jadi gampang terlewat.
  Jalankan `waifu say "Halo Master"` (tanpa `--fallback`) dan baca baris
  `The id voice failed ...`: alasannya ada di ujung baris itu. Kalau isinya
  "Microsoft menolak suara ... (403)", edge-tts di PC ini sudah usang dan tidak pulih sendiri:
  jalankan `.venv\Scripts\python -m pip install -U edge-tts`, lalu mulai ulang `waifu run`.
- Suara "natural" Narrator tidak bisa dipakai: Windows tidak membukanya untuk aplikasi lain.

## Avatar 3D (つくよみちゃん)

Dengan `avatar.enabled: true`, selama Cyrene berjalan seorang gadis anime 3D, model
resmi つくよみちゃん, berdiri di pojok desktop: tampak transparan, selalu di atas jendela
lain, dan bisa digeser. Dia bernapas, berkedip, dan menatapmu saat diam; tampak berpikir
selama menyusun jawaban; mulutnya bergerak mengikuti setiap jawaban yang diucapkan; dan
wajahnya memasang ekspresi jawaban itu. Persona meminta model mengawali setiap jawaban
dengan satu tag ekspresi (`[senang]`, `[sedih]`, `[marah]`, `[kaget]`, `[santai]`,
`[netral]`); tag itu tidak pernah diucapkan atau ditampilkan di `waifu chat`.

- Cyrene melayani halaman avatar di `http://127.0.0.1:9882/` (hanya dari PC ini).
  Maskotnya aplikasi Electron kecil di folder `avatar-app\` yang menampilkan halaman itu
  di jendela transparan tanpa bingkai.
- `waifu run`, `waifu say`, dan `waifu chat --speak` membuka maskot (`run` melakukannya
  saat memuat model, sebelum bilang siap) dan menutupnya saat Cyrene berhenti: Ctrl+C di
  terminal, atau jendela konsol "Cyrene" dari autostart ditutup.
- `waifu say` dan sapaan "Cyrene siap, Master." dari autostart menunggu maskot selesai
  dimuat, paling lama 5 detik, sebelum bicara, supaya mulutnya ikut bergerak dari kata
  pertama. Biasanya tidak terasa, karena suara anime (RVC) butuh waktu lebih lama untuk
  siap.
- **Dia tidak pernah diam karena avatar.** Kalau avatar gagal jalan, muncul
  `! Avatar tidak tersedia: ... Cyrene tetap berjalan tanpa avatar.` dan dia tetap
  mendengar dan bicara seperti biasa.

### Hak model (baca dulu)

- Model: **つくよみちゃん公式3Dモデル タイプA** (© Rei Yumesaki). Ketentuannya ada di
  halaman unduhannya, <https://tyc.rei-yumesaki.net/material/avatar/3d-a/>; baca dulu.
- Pemakaian pribadi seperti ini boleh. Kredit **つくよみちゃん（© Rei Yumesaki）**
  selalu tampil kecil di pojok halaman avatar; jangan dihapus.
- **Jangan menyebarkan ulang atau mengunggah** file model ke mana pun (termasuk VRoid
  Hub atau repo ini). File VRM tinggal di `C:\AI\avatars\`, di luar repo, dan tidak
  pernah di-commit.

### 1. Unduh model (sekali)

Dari <https://tyc.rei-yumesaki.net/material/avatar/3d-a/>, unduh paket modelnya
(`tyc-3d-a.zip`). Yang dipakai Cyrene adalah versi **01 通常版（VRM）**, file
`【輪郭線あり】つくよみちゃん公式3Dモデル タイプA（通常版）.vrm` (dengan garis tepi).

**Jangan ekstrak dengan "Extract All" di Explorer atau `Expand-Archive`.** Nama file di
zip ini memakai encoding Jepang (Shift-JIS) tanpa penanda, jadi Windows berbahasa
Inggris/Indonesia mengubahnya jadi huruf acak (`é┬é¡éµ…`), dan `Expand-Archive` bahkan
gagal di tengah jalan. Ekstrak dengan perintah ini, yang membaca namanya sebagai
Shift-JIS (kode 932), lalu salin file VRM-nya menjadi `C:\AI\avatars\tsukuyomi_a.vrm`:

```powershell
New-Item -ItemType Directory -Force C:\AI\avatars
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory("$HOME\Downloads\tyc-3d-a.zip", "C:\AI\avatars\tyc-3d-a", [Text.Encoding]::GetEncoding(932))
Copy-Item "C:\AI\avatars\tyc-3d-a\つくよみちゃん公式3Dモデル タイプA\01 通常版（VRM）\【輪郭線あり】つくよみちゃん公式3Dモデル タイプA（通常版）.vrm" C:\AI\avatars\tsukuyomi_a.vrm
```

Ganti path zip-nya kalau tidak ada di folder Downloads. 7-Zip juga bisa dipakai. Satu
file teks di folder `03 Recotte Studio用（VRM）` tetap bernama aneh
(`肩周辺の・示が…txt`) karena namanya memang rusak di dalam zip; itu wajar dan tidak
dipakai.

### 2. Pasang Electron (sekali)

Butuh [Node.js](https://nodejs.org/) (versi LTS atau lebih baru). Dari folder repo:

```powershell
Set-Location avatar-app
npm install
node node_modules/electron/install.js
Set-Location ..
```

`npm install` memasang Electron 44 ke `avatar-app\node_modules\`, tapi di PC ini npm
melewati skrip unduhan Electron, jadi `node node_modules/electron/install.js` yang
mengunduh programnya ke `node_modules\electron\dist\`. Folder `node_modules\` tidak
di-commit.

### 3. Aktifkan di config.yaml

```yaml
avatar:
  enabled: true
  model: C:/AI/avatars/tsukuyomi_a.vrm
  window: mascot          # mascot | app (jendela Edge) | none (halaman saja)
  width: 360
  height: 640
  corner: bottom-right    # posisi awal maskot
  always_on_top: true
  framing: full           # full: seluruh badan | upper: setengah badan
  port: 9882
```

Penjelasan tiap kunci ada di `config.example.yaml`. Lalu cek:

```powershell
waifu check            # ✓ Mesin avatar: VRM 3D, ✓ Model VRM avatar, ✓ Jendela maskot avatar (Electron), ✓ Port avatar bebas
waifu avatar --demo    # avatar saja, tanpa mendengarkan; Ctrl+C untuk berhenti
```

`waifu avatar --demo` mengganti ekspresi (netral, senang, sedih, marah, kaget, santai)
dan keadaan (diam, mendengarkan, berpikir, bicara) setiap 3 detik, dengan mulut yang
bergerak melewati semua bentuk vokal, jadi kamu bisa mengecek modelnya tanpa bicara.
`waifu avatar` tanpa `--demo` hanya menampilkannya diam. Untuk mengecek modelnya, pakai
perintah ini. Satu ekspresi dengan suara bisa dicoba lewat
`waifu say "[senang] Halo Master!"` (tag-nya tidak diucapkan): `say` menunggu maskot
dimuat (paling lama 5 detik), bicara, lalu langsung menutup maskotnya. Kalau maskot
belum muncul dalam 5 detik (misalnya PC sedang sibuk), dia tetap bicara tanpa gerakan
mulut.

### Kontrol maskot

- **Geser:** tekan badannya lalu tarik.
- **Area transparan di sekitarnya tidak menghalangi:** klik di sana tembus ke jendela
  atau desktop di bawahnya. Elus kepalanya, colek badannya, atau klik dua kali supaya
  dia langsung mendengarkan: semua kontrol mouse ada di
  [Avatar Live2D & interaksi](#kontrol-mouse-kedua-engine) dan berlaku juga untuk VRM.
- **Potong omongannya:** klik dua kali padanya selagi dia bicara (jawaban panjang,
  pengumuman, atau pengingat). Suaranya langsung berhenti, mulutnya menutup, dan dia
  langsung mendengarkan tanpa berkata apa-apa, jadi kamu bisa bicara tanpa memanggil
  namanya (hanya di `waifu run`). Pengingat yang masuk saat itu menunggu sampai
  omonganmu terdengar atau jendela lanjutan habis, jadi kalimat pertamamu tidak hilang.
  Dengan suara dia **tidak** bisa dipotong: selama dia bicara mic dijeda supaya dia tidak
  mendengar suaranya sendiri.
- **Sembunyikan:** arahkan mouse ke pojok kanan atas maskot, lalu klik **×**. Hanya
  maskotnya yang tertutup; Cyrene tetap mendengar dan menjawab dengan suara. Maskot
  muncul lagi pada `waifu run` berikutnya.
- Di taskbar dan Alt+Tab maskot bernama **"Avatar Cyrene"**. Menutupnya (× atau
  Alt+F4) sama dengan menyembunyikannya; yang menghentikan Cyrene adalah jendela konsol
  "Cyrene" atau Ctrl+C di terminal `waifu run`.
- Titik di sebelah × hijau kalau maskot terhubung ke Cyrene, merah kalau terputus (dia
  menyambung lagi sendiri). Titik itu baru hijau setelah modelnya selesai dimuat.
- Maskot ikut tertutup saat Cyrene berhenti (Ctrl+C, atau jendela konsol "Cyrene" dari
  autostart ditutup).

**Tanpa Electron:** `avatar.window: app` menampilkan avatar di jendela aplikasi Microsoft
Edge biasa (berlatar, berbingkai, bisa diubah ukurannya) dengan profil Edge sendiri di
`data\avatar-edge-profile`, jadi browser-mu tidak tersentuh. `avatar.window: none` hanya
melayani halamannya; buka `http://127.0.0.1:9882/` sendiri di browser.

### Pemecahan masalah avatar

- **"Avatar tidak tersedia: Port 127.0.0.1:9882 sudah dipakai ...".** Cyrene lain
  (misalnya dari autostart) atau program lain memakai port itu. Cek dengan
  `netstat -ano | findstr :9882`, tutup programnya, atau ganti `avatar.port`.
- **"Program maskot (Electron) tidak ditemukan".** Jalankan langkah 2.
- **Latar hitam, bukan transparan.** Jendela transparan Electron butuh akselerasi GPU
  yang berjalan baik: perbarui driver NVIDIA. Sementara itu, pakai `avatar.window: app`.
- **Pesan "Pustaka avatar tidak bisa dimuat" di maskot.** Ada file yang hilang di
  `src\waifu\avatar\web\vendor\` (lihat `VENDOR.md` di sana). Ambil ulang folder itu dari
  repo; `pytest tests/test_avatar_web.py` menyebut file yang hilang.
- **Pesan "Model avatar tidak bisa dimuat" di maskot.** `avatar.model` menunjuk file
  yang tidak ada atau bukan VRM. File yang tidak ada ditandai `waifu check` dengan
  `✗ Model VRM avatar tidak ditemukan` beserta path-nya.
- **Maskot diam saja saat dia bicara.** Titik di pojok kanan atas merah berarti maskot
  terputus dari Cyrene; tunggu sebentar atau jalankan ulang `waifu run`.
- **Mematikan avatar:** `avatar.enabled: false`. Model dan Electron tidak perlu dihapus.

#### Cyrene hilang saat main game

Game dengan mode layar penuh eksklusif (*Fullscreen*, misalnya Genshin Impact, Honkai:
Star Rail, dan Zenless Zone Zero) selalu tampil di atas semua jendela, jadi selama game
itu tampil, maskot di monitor yang sama memang tertutup. Itu disengaja, karena tombol
skill game ada di pojok kanan bawah. Yang dilakukan maskot supaya dia kembali sendiri:

- **Dia terus menggambar dirinya** walaupun Chromium (mesin di dalam Electron) mengira
  jendelanya tertutup: pelacakan jendela tertutup (*native window occlusion*) dan
  pelambatan halaman di latar belakang dimatikan. Begitu tidak ada yang menutupinya (di
  monitor lain, atau game diminimalkan lewat Alt+Tab atau ditutup), dia langsung
  terlihat lagi tanpa menjalankan ulang Cyrene. Di mode maskot dia menggambar paling
  banyak 30 kali per detik, jadi menggambar di balik game hampir tidak memakan GPU.
- **Grafisnya hilang** (bisa terjadi saat game mengganti mode layar): halamannya dimuat
  ulang sendiri begitu grafisnya pulih, atau 3 detik kemudian kalau tidak pulih. Chromium
  tidak diizinkan memblokir WebGL untuk halaman Cyrene setelah GPU di-reset, jadi halaman
  yang dimuat ulang bisa menggambar dia lagi.
- **Halamannya tidak mendapat WebGL sama sekali** (maskot menampilkan "WebGL tidak
  tersedia di jendela ini…"): selama Electron masih punya WebGL, halamannya dimuat ulang
  (ikut batas 5 kali dalam 10 menit di bawah). Kalau Electron sudah menyerah pada GPU
  (proses GPU-nya terlalu sering mati), program maskot keluar dengan kode 1 supaya Cyrene
  membukanya lagi dengan Electron yang baru (lihat poin terakhir). Kalau WebGL memang
  tidak pernah tersedia (driver GPU bermasalah), maskot dibuka ulang 3 kali lalu dibiarkan
  tertutup: perbarui driver NVIDIA.
- **Proses GPU atau proses halaman Electron mati:** halamannya dimuat ulang setelah
  1 detik, paling banyak 5 kali dalam 10 menit (setelah itu hanya dicatat di log sebagai
  `page-reload skipped`, dan dia baru muncul lagi setelah `waifu run` dijalankan ulang).
- **Resolusi monitor berubah**, atau monitor ditambah atau dicabut: "selalu di atas"
  dipasang lagi saat itu juga, sekali lagi 1,5 detik setelah perubahan terakhir, dan setiap
  kali jendelanya ditampilkan atau dipulihkan dari minimize. (Di Windows, "selalu di atas"
  maskot hanya berlaku selama taskbar juga di atas, dan Windows menurunkan taskbar selama
  game layar penuh berjalan di monitor utama; karena itu dipasang lagi setelah semuanya
  tenang.) Kalau maskot tidak lagi terlihat di layar mana pun, dia dipindah ke pojoknya
  (`avatar.corner`) di monitor utama; begitu tempatnya semula terlihat lagi (game
  mengembalikan resolusi monitor, atau monitor yang tidur menyala lagi) dan kamu belum
  menggesernya, dia dikembalikan ke tempat semula. Dia tetap bersembunyi di balik game
  layar penuh.
- **Program maskot (Electron) keluar karena error:** Cyrene membukanya lagi setelah
  3 detik, paling banyak 3 kali dalam 10 menit. Setelah itu maskot dibiarkan tertutup
  dan log menulis `... so it stays closed. Restart Cyrene to bring it back.`: jalankan
  ulang `waifu run`. Maskot yang kamu tutup sendiri (× atau Alt+F4) tidak dibuka lagi.

Semua kejadian itu dicatat di jendela `waifu run` (dan di `logs\waifu.log` kalau Cyrene
jalan lewat autostart atau dengan `--log-file`), satu baris per kejadian yang diawali
`Cyrene mascot:`:

| Baris | Artinya |
|---|---|
| `Cyrene mascot: page-hidden` / `page-visible` | Chromium mengira halamannya tersembunyi / terlihat lagi |
| `Cyrene mascot: webgl-context-lost` / `webgl-context-restored` | grafis WebGL halamannya hilang / pulih |
| `Cyrene mascot: webgl-unavailable` | halamannya tidak mendapat WebGL sama sekali ("WebGL tidak tersedia…") |
| `Cyrene mascot: exiting webgl=...` | Electron sudah tidak punya WebGL; program maskot keluar supaya Cyrene membuka yang baru |
| `Cyrene mascot: page-reload ...` | halamannya dimuat ulang (atau `skipped`: sudah 5 kali dalam 10 menit, dan dia tetap hilang sampai `waifu run` dijalankan ulang) |
| `Cyrene mascot: render-process-gone reason=... exit-code=...` | proses halaman mati, dengan sebab dan kode keluarnya |
| `Cyrene mascot: gpu-process-gone reason=... exit-code=...` | proses GPU Electron mati |
| `Cyrene mascot: unresponsive` / `responsive` | halamannya macet / jalan lagi |
| `Cyrene mascot: display-metrics-changed` / `display-added` / `display-removed` | monitor berubah (resolusi, ditambah, atau dicabut), dengan ukurannya |
| `Cyrene mascot: placed-back ...` | maskot dipindah ke pojoknya di monitor utama, atau dikembalikan ke tempatnya semula (`where she was before`) |
| `Cyrene mascot: re-pinned after ...` | "selalu di atas" dipasang lagi (setelah perubahan monitor selesai, atau saat jendelanya ditampilkan atau dipulihkan) |
| `The avatar mascot (pid ...) exited with code ...; reopening it in 3 s` | program maskot keluar karena error dan dibuka lagi |

Baris `Cyrene mascot:` yang melaporkan sesuatu mati, hilang, macet, tidak tersedia, atau
dilewati (kata *gone*, *lost*, *unresponsive*, *crashed*, *unavailable*, atau *skipped*)
tampil sebagai `WARNING`, yang lain sebagai `INFO`. Keluaran Chromium lainnya hanya tampil
dengan `-v`.

**Kalau dia tetap hilang:** untuk memunculkannya lagi sekarang, jalankan ulang Cyrene:
hentikan dia (Ctrl+C di terminal `waifu run`, atau tutup jendela konsol "Cyrene"), lalu
jalankan lagi. Itu juga yang harus dilakukan kalau maskot menampilkan "WebGL tidak
tersedia di jendela ini…" atau log menulis `page-reload skipped` atau
`... so it stays closed`. Menutup maskot dengan × atau Alt+F4 tidak membukanya lagi.
Lalu salin baris `Cyrene mascot:` dan `The avatar mascot` dari sekitar waktu dia hilang,
dan kirimkan saat melaporkan masalahnya, bersama nama game, monitor tempat game dan
maskot berada, dan apa yang kamu lakukan (Alt+Tab, memindahkan maskot). Tanpa baris itu
sebabnya tidak bisa diketahui. Sementara itu, pakai mode *Borderless* (*windowed
fullscreen*) atau *Windowed* di pengaturan grafis game: dengan mode itu dia tetap
terlihat di monitor lain tanpa semua hal di atas. Di monitor game dia tampil di atas
game, jadi geser dia kalau menutupi tombol game.

## Avatar Live2D & interaksi

Selain VRM 3D, avatar bisa memakai model **Live2D** (Cubism 5) dengan
`avatar.engine: live2d`, misalnya model Cyrene "Cyrene20251105" buatan MomokaSono. Dengan
kedua engine, maskotnya juga bisa disentuh: dia menatap kursor mouse di mana pun di
layar, senang saat kepalanya dielus, kaget saat badannya dicolek (wajahnya berubah saat
itu juga, lalu dia mengucapkan satu kalimat pendek), langsung mendengarkan saat diklik
dua kali, tampak bosan kalau lama tidak ada aktivitas, dan menyapa saat Master kembali.

- Live2D digambar dengan PixiJS 8 dan untitled-pixi-live2d-engine (keduanya MIT, sudah
  ada di `src\waifu\avatar\web\vendor\`). Yang **tidak** ikut adalah **Cubism Core**
  (`live2dcubismcore.min.js`), program milik Live2D yang dibutuhkan semua model Cubism:
  file itu kamu unduh sendiri (langkah 1).
- Kalau Core atau modelnya belum ada, hanya avatarnya yang menampilkan pesan; Cyrene
  tetap mendengar dan bicara seperti biasa.

### Hak model dan Cubism Core (baca dulu)

- **Model Cyrene20251105 (Live2D: MomokaSono).** Ketentuannya ada di file `注意事项.txt`
  di folder model: hak ciptanya milik **miHoYo**; boleh dipakai untuk membuat video
  terkait miHoYo selama mengikuti aturan karya penggemar resmi miHoYo dan bukan untuk
  propaganda politik; **dilarang dipakai untuk siaran langsung yang menghasilkan uang**;
  **dilarang disebarkan ulang**; segala pelanggaran hukum atau aturan platform menjadi
  tanggung jawab pemakainya.
- Pemakaian di sini pribadi dan non-komersial. Folder model tinggal di
  `C:\AI\avatars\`, di luar repo, dan **tidak pernah di-commit, diunggah, atau
  dibagikan** ke mana pun.
- **Kredit:** isi `avatar.credit`, misalnya `Cyrene © miHoYo · Live2D: MomokaSono`. Kredit
  itu selalu tampil kecil di pojok halaman avatar; jangan dihapus.
- Interaksinya hanya punya dua area, **kepala** (dielus) dan **badan** (dicolek), dengan
  reaksi yang sopan. Tidak ada interaksi bernuansa dewasa, dan tidak akan ditambahkan:
  aturan karya penggemar miHoYo melarang konten dewasa (18+). Area kepala diukur dari
  modelnya sendiri saat dimuat: dari ujung rambut sampai sedikit di atas dagu. Semua yang
  di bawah dagu, termasuk dada dan rambut yang menjuntai di depannya, adalah badan.
- **Cubism Core** milik Live2D dan tidak boleh disebarkan ulang. Unduh sendiri dari situs
  resmi Live2D dan setujui lisensinya saat mengunduh. Cyrene hanya menyajikannya apa
  adanya dari `avatar.live2d_core` ke halaman avatar di PC ini. **Jangan pernah menyalin
  Core ke folder repo**: `.gitignore` sudah menolak `live2dcubismcore*.js`, dan
  `waifu check` mengingatkannya.

### 1. Unduh Cubism SDK for Web (sekali)

1. Buka <https://www.live2d.com/en/sdk/download/web/>, baca dan setujui lisensinya,
   lalu unduh **Cubism SDK for Web** versi 5 (model ini memakai format moc3 Cubism 5,
   yang tidak bisa dibaca Core versi 4).
2. Ekstrak zip-nya di Downloads (klik kanan → **Extract All**). Explorer membuat folder
   `CubismSdkForWeb-5-r.5` yang berisi folder `CubismSdkForWeb-5-r.5` lagi, dan `Core\`
   ada di dalamnya. Salin **hanya** `live2dcubismcore.min.js` dari situ ke luar repo.
   Perintah ini mencarinya di tingkat folder mana pun, dan berhenti dengan pesan galat
   kalau file-nya tidak ada (bukan diam-diam tidak menyalin apa-apa):

```powershell
New-Item -ItemType Directory -Force C:\AI\avatars\live2d
$core = Get-ChildItem "$HOME\Downloads\CubismSdkForWeb-*" -Directory | Get-ChildItem -Recurse -Filter live2dcubismcore.min.js | Select-Object -First 1
if (-not $core) { throw "live2dcubismcore.min.js tidak ditemukan; ekstrak dulu CubismSdkForWeb-*.zip di Downloads" } else { Copy-Item $core.FullName C:\AI\avatars\live2d\ }
Test-Path C:\AI\avatars\live2d\live2dcubismcore.min.js   # harus True
```

Kalau SDK diekstrak di tempat lain, ganti `$HOME\Downloads` di baris kedua. Bagian lain
SDK (Framework, Samples) tidak dipakai.

### 2. Taruh folder model di C:\AI\avatars\ (sekali)

Salin **seluruh** folder model, bukan hanya `.model3.json`: model3.json memanggil moc3,
physics, dan teksturnya dari foldernya sendiri. Untuk model Cyrene dari Downloads:

```powershell
Copy-Item -Recurse "$HOME\Downloads\Cyrene20251105_by_MomokaSono_*\Cyrene20251105" C:\AI\avatars\cyrene-live2d
```

Kalau `C:\AI\avatars\cyrene-live2d\Cyrene.model3.json` sudah ada, langkah ini sudah
selesai (menjalankannya lagi membuat subfolder di dalamnya). Isinya:
`Cyrene.model3.json`, `Cyrene.moc3`, `Cyrene.physics3.json`, `Cyrene.cdi3.json`, folder
`Cyrene.4096\` (empat tekstur), dan `注意事项.txt`. Dari folder ini Cyrene hanya
menyajikan file `.json`, `.png`, dan `.moc3` ke halaman avatar.

### 3. Aktifkan di config.yaml

Electron dipasang seperti [langkah 2 Avatar 3D](#2-pasang-electron-sekali). Lalu:

```yaml
avatar:
  enabled: true
  engine: live2d
  model: C:/AI/avatars/cyrene-live2d/Cyrene.model3.json
  live2d_core: C:/AI/avatars/live2d/live2dcubismcore.min.js
  credit: "Cyrene © miHoYo · Live2D: MomokaSono"
  window: mascot
  framing: upper          # upper: setengah atas badan memenuhi tinggi jendela | full: seluruh model
```

Untuk Cyrene20251105 pakai `framing: upper`: sayapnya membuat model ini jauh lebih lebar
daripada jendela maskot, jadi dengan `full` dia tampil kecil (wajahnya sekitar 25 piksel di
jendela 360×640). Dengan `upper`, kepala sampai lutut memenuhi tinggi jendela dan ujung
sayap terpotong di sisi kiri-kanan.

Lalu cek:

```powershell
waifu check            # ✓ Mesin avatar: Live2D, ✓ Model Live2D avatar, ✓ File model Live2D, ✓ Cubism Core Live2D
waifu avatar --demo    # ekspresi berganti setiap 3 detik; coba elus kepalanya dan colek badannya
```

Di `waifu avatar`, reaksinya dicetak di terminal (`Cyrene: Hehe, makasih elusannya,
Master.`) dan tampil di maskot dengan ekspresi dan gerak mulut, tanpa suara. Kembali ke
VRM: `engine: vrm`, `model` ke file `.vrm`, dan `credit` ke kredit つくよみちゃん.

### Kontrol mouse (kedua engine)

| Gerakan | Hasilnya |
|---|---|
| Tekan **badannya** lalu tarik | Maskot bergeser (dari kepala: tarik sampai keluar kepala) |
| Klik **kepalanya**, atau usap bolak-balik sambil menekan | Dielus: wajah senang, lalu satu kalimat elusan (saat diusap paling sering tiap 1,5 detik) |
| Klik **badannya** | Dicolek: wajah kaget, lalu satu kalimat |
| **Klik dua kali** padanya (dua klik dalam setengah detik) | Dia langsung mendengarkan: jendela lanjutan terbuka dan dia menjawab dengan kalimat `listen` (misalnya "はい, Master?"), jadi kamu bisa bicara tanpa memanggil namanya (hanya di `waifu run`). Selagi dia **bicara**, klik dua kali memotongnya: suaranya langsung berhenti dan dia langsung mendengarkan, tanpa kalimat `listen`. Selagi dia masih memikirkan jawaban atau mendengar Master, klik dua kali diabaikan: ulangi setelah dia selesai, atau panggil namanya |
| Klik di **area transparan** | Tembus ke jendela atau desktop di bawahnya; hanya tubuhnya dan tombol di pojok kanan atas yang menangkap klik |
| Gerakkan mouse | Mata, kepala, dan badannya mengikuti kursor di mana pun di layar |
| Arahkan ke pojok kanan atas, klik **×** | Maskot disembunyikan; Cyrene tetap berjalan, maskot muncul lagi di `waifu run` berikutnya |

- **Bosan:** setelah 3 menit tanpa gerakan mouse, klik, atau bicara, dia sesekali menguap.
- **Selamat datang kembali** (hanya maskot): kalau mouse bergerak lagi, atau maskot diklik,
  setelah 10 menit mouse diam di mana pun di layar (`welcome_back_after_min`), dia
  menyapa, paling sering sekali per 30 menit. Selama maskot di-minimize dia tidak melihat
  kursor, jadi membukanya lagi tidak dianggap Master baru kembali.
- Kalimat reaksi diucapkan lewat jalur bicara biasa: mic dijeda, mulut avatar bergerak,
  lalu jendela lanjutan terbuka. Dia **tidak** bereaksi dengan suara saat sedang
  menjawab, sedang bicara, atau saat Master sedang bicara, dan memberi jeda
  `reaction_cooldown_s` antar-reaksi; ekspresi wajahnya tetap langsung muncul.
- Di `avatar.window: app` (jendela Edge) atau browser, kursor hanya diikuti selama berada
  di atas halaman, maskot tidak bisa digeser dengan menarik badannya, tidak ada klik
  tembus, dan tidak ada sapaan selamat datang kembali (halaman tidak tahu apakah mouse
  diam atau sedang dipakai di jendela lain).

### Pengaturan interaksi

Semua kunci ada di bawah `avatar.interactions` (contoh lengkap di `config.example.yaml`):

| Kunci | Bawaan | Arti |
|---|---|---|
| `enabled` | `true` | `false` mematikan semua reaksi; maskot tetap bisa digeser dan tetap tembus klik |
| `cursor_follow` | `true` | Menatap kursor mouse |
| `head_pat` | `true` | Reaksi elusan di kepala |
| `body_touch` | `true` | Reaksi colekan di badan |
| `double_click_listen` | `true` | Klik dua kali membuatnya mendengarkan, dan memotongnya kalau dia sedang bicara |
| `idle` | `true` | Tampak bosan setelah 3 menit tanpa aktivitas |
| `welcome_back_after_min` | `10` | Sapaan saat Master kembali setelah N menit mouse diam (hanya maskot); `0` = tidak pernah |
| `reaction_cooldown_s` | `3` | Jeda minimal (detik) antar-reaksi bersuara |
| `lines` | lihat di bawah | Kalimat tiap reaksi |

```yaml
avatar:
  interactions:
    lines:
      head_pat: ["えへへ, geli, {owner}!", "Hehe, makasih elusannya, {owner}.", "Mm, enak dielus begini."]
      body_touch: ["Hei! Kaget aku, {owner}.", "Eh? Ada apa, {owner}?", "Jangan iseng, ya!"]
      welcome_back: ["おかえりなさい, {owner}!", "{owner} sudah kembali! Aku kangen."]
      listen: ["はい, {owner}?", "Iya, aku dengar."]
```

`{owner}` diganti dengan `assistant.owner_call` saat diucapkan; kalimat yang memuatnya
wajib diberi tanda kutip. Kalimat yang kamu salin dulu dari contoh lama masih menulis
"Master" langsung: ganti dengan `{owner}`, atau hapus jenisnya untuk memakai bawaannya.
`waifu check` memperingatkan kalimat seperti itu selama `owner_call` bukan `Master`.
Kalimat dipilih acak dan tidak pernah sama dua kali berturut-turut. Bagian Jepang
diucapkan dengan suara Jepang seperti jawaban biasa. Jangan menulis tag ekspresi
(`[senang]`) di sini: ekspresinya sudah tetap (elusan, sapaan, dan `listen` senang;
colekan kaget). Jenis yang dihapus memakai kalimat bawaannya; setiap jenis butuh minimal
satu kalimat.

### Pemecahan masalah Live2D dan interaksi

- **"Cubism Core belum ada, jadi model Live2D tidak bisa ditampilkan"** di maskot, atau
  `✗ Cubism Core Live2D tidak ditemukan` di `waifu check`. Core belum disalin atau
  `avatar.live2d_core` salah: ulangi [langkah 1](#1-unduh-cubism-sdk-for-web-sekali) dan
  pakai garis miring `/` di path-nya.
- **"Model Live2D tidak bisa dimuat dari ..."** di maskot. `waifu check` menunjukkan
  sebabnya: `✗ Model Live2D avatar tidak ditemukan` berarti `avatar.model` salah (harus
  menunjuk file `.model3.json`); `✗ File model Live2D tidak ditemukan: ...` menyebut file
  yang hilang, biasanya karena hanya model3.json yang tersalin. Salin ulang seluruh
  folder model ([langkah 2](#2-taruh-folder-model-di-caiavatars-sekali)). Model dengan
  moc3 Cubism 5 juga butuh Core dari SDK versi 5. Core 6 dari SDK 5-r.5 sudah didukung;
  model yang memakai fitur *offscreen* atau *blend mode* baru Cubism 5.3 belum bisa
  digambar engine ini (konsol halaman menyebut "offscreens" atau "blend modes").
- **Cyrene kecil sekali di maskot.** Model dengan sayap, jubah, atau properti lebar dipaskan
  utuh ke jendela pada `framing: full`. Pakai `framing: upper` (lalu perbesar
  `avatar.height` kalau perlu; `avatar.width` hanya menambah bagian sayap yang terlihat),
  atau tetap `framing: full` dan perbesar `avatar.width` (menambah `avatar.height` saja
  malah membuatnya lebih kecil).
- **Latar hitam, bukan transparan.** Sama seperti avatar 3D: jendela transparan Electron
  butuh akselerasi GPU yang berjalan baik, jadi perbarui driver NVIDIA. Sementara itu,
  pakai `avatar.window: app`.
- **Klik tidak tembus / tertahan maskot.** Klik tembus hanya ada di maskot Electron
  (`avatar.window: mascot`). Selama model belum tampil (masih memuat atau ada pesan
  galat), seluruh jendela maskot sengaja menangkap klik supaya pesannya bisa dibaca dan
  × bisa diklik. Begitu model tampil, gerakkan kursor sekali di atas maskot: area
  transparan langsung tembus lagi. Kalau tetap tertahan, tutup maskot dengan × dan
  jalankan ulang `waifu run`.
- **Elusan atau colekan tidak dijawab dengan suara.** Wajahnya berubah tapi dia diam:
  dia sedang sibuk (menjawab, bicara, atau mendengar Master) atau masih dalam jeda
  `reaction_cooldown_s`. Tidak ada perubahan wajah sama sekali: cek
  `avatar.interactions.enabled` dan sakelar `head_pat` / `body_touch`.
- **Hilang saat main game dan tidak kembali.** Lihat
  [Cyrene hilang saat main game](#cyrene-hilang-saat-main-game).
- **Tidak menatap kursor.** Cek `cursor_follow: true`; di jendela app atau browser dia
  hanya menatap kursor yang ada di atas halaman.

## Teman main gacha (HoYoverse)

Cyrene ikut menemani Master main **Honkai: Star Rail**, **Genshin Impact**, dan
**Zenless Zone Zero** (server Asia): dia tahu stamina dan daily tiap game, mengingatkan
sebelum stamina terbuang, mengambil check-in harian HoYoLAB, dan membaca riwayat pull
untuk menghitung pity dan 50/50.

```text
Master : "Cyrene, Trailblaze Power-ku berapa?"
Cyrene : "Dua ratus tiga puluh dua dari tiga ratus, Master. Penuh sekitar jam setengah dua."
Master : "Cyrene, aku barusan pull di HSR. Pity-ku sekarang gimana?"
Cyrene : "Pull barusan belum muncul, Master, karena HoYoverse biasanya telat sekitar satu
          jam. Pity banner karakter dua puluh dua, jadi masih enam puluh delapan pull lagi
          ke hard pity, dan berikutnya belum guaranteed. がんばって!"
(20.00)  Cyrene : "Master, Daily Training HSR belum selesai, baru 300 dari 500. Reset jam 03:00 nanti."
```

Yang dia lakukan:

- **Status kapan saja.** Stamina (Trailblaze Power, Original Resin, Battery Charge) dan
  kapan penuh, progres daily, sisa mingguan, ekspedisi, dan status check-in. Dibaca
  langsung dari HoYoLAB, tidak pernah ditebak.
- **Pengingat lewat suara dan HP.** Selama `waifu run` jalan, dia bicara sendiri di sela
  kalimatmu (tidak pernah memotong jawaban atau omonganmu) dan mengirim teks yang sama
  ke HP lewat ntfy. Tiap kejadian sekali saja; tanpa jam tenang.
- **Check-in harian HoYoLAB otomatis**, sekali sehari untuk tiap game.
- **Riwayat pull.** Pity tiap banner, sisa ke hard pity, apakah 5★ berikutnya pasti
  limited, dan reaksi seperti teman: senang kalau menang 50/50, menghibur kalau kalah.
- **Akun game.** Karakter milik Master dengan Eidolon, Constellation, atau Mindscape
  Cinema dan Light Cone, weapon, atau W-Engine-nya, progres endgame musim ini, serta banner
  dan event yang sedang jalan, untuk ketiga game (lihat "Data akun HSR" dan "Data akun
  Genshin dan ZZZ").
- **Kode redeem.** Kode baru ketiga game diberitahukan lewat suara dan HP, dan ditukarkan
  di HoYoLAB hanya kalau Master minta (lihat "Kode redeem").
- **Pendapatan bulanan.** Primogem, Stellar Jade, dan Polychrome yang didapat bulan ini atau dua
  bulan sebelumnya, dari catatan bulanan HoYoLAB, hanya kalau ditanya (lihat "Pendapatan gacha
  bulanan").
- **Laporan mingguan.** Seminggu sekali dia bercerita sendiri tentang pull, pity, endgame,
  dan yang segera berakhir di tiap game (lihat "Laporan game mingguan").
- **Tidak pernah menyarankan top-up** atau membelanjakan uang.

Semuanya hanya jalan selama PC menyala.

### 1. Ambil cookie HoYoLAB

1. Buka https://www.hoyolab.com di Chrome atau Edge, lalu login.
2. Tekan **F12** → tab **Application** → **Storage** → **Cookies** →
   `https://www.hoyolab.com`.
3. Salin nilai `ltuid_v2`, `ltoken_v2`, `ltmid_v2`, `cookie_token_v2`, `account_id_v2`,
   dan `account_mid_v2`. Beberapa bertanda HttpOnly, jadi tidak muncul lewat
   `document.cookie`: ambil dari tabel ini.
4. Tulis di `config.yaml` sebagai satu baris:
   `cookie: "ltuid_v2=...; ltoken_v2=...; ltmid_v2=...; cookie_token_v2=...; account_id_v2=...; account_mid_v2=..."`.

`ltuid_v2` dan `ltoken_v2` wajib (status, pengingat, check-in). `cookie_token_v2`
membuat riwayat pull tetap terbaca walau riwayat di game belum dibuka hari ini.

> **Cookie ini setara kata sandi akun HoYoLAB.** Jangan dibagikan. `config.yaml` tidak
> di-commit, dan Cyrene tidak pernah menulis cookie ke log. Login di browser lain bisa
> membuat `cookie_token_v2` tidak berlaku; Cyrene akan bilang kalau cookie perlu diambil
> ulang.

### 2. Nyalakan Real-Time Notes

Di HoYoLAB: **Battle Chronicle** → ikon pengaturan → aktifkan **Real-Time Notes** untuk
HSR, Genshin, dan ZZZ. Cyrene tidak pernah mengubah setelan HoYoLAB sendiri; kalau belum
aktif, dia bilang game mana yang perlu dinyalakan.

### 3. Notifikasi ke HP (ntfy)

1. Pasang aplikasi **ntfy** dari Play Store atau App Store (gratis, tanpa akun).
2. Buat topik rahasia:
   ```powershell
   .venv\Scripts\python -c "import secrets; print('cyrene-' + secrets.token_urlsafe(12))"
   ```
3. Di aplikasi ntfy: **+** → isi topik itu (server bawaan `ntfy.sh`).
4. Isi `notify` di `config.yaml` (langkah 4), lalu `waifu notify-test`: notifikasi tes
   harus masuk ke HP.

Atau jalankan `waifu setup notify`: topiknya dibuat, ditampilkan bersama langkah berlangganan,
dan ditulis ke `config.yaml` dengan `notify.enabled: true`; notifikasi tes dikirim kalau kamu
jawab `y` (lihat [Setup pertama](#setup-pertama)).

Server publik `ntfy.sh` mengantar pesan ke siapa pun yang tahu nama topiknya, jadi
topiknya harus panjang dan acak (Cyrene menolak topik yang terlalu pendek) dan jangan
dibagikan. Isinya hanya pengingat game.

### 4. Aktifkan di config.yaml

```yaml
gacha:
  enabled: true
  cookie: "ltuid_v2=...; ltoken_v2=...; ltmid_v2=...; cookie_token_v2=...; account_id_v2=...; account_mid_v2=..."
  games: [hsr, genshin, zzz]
  reminders:
    stamina_minutes: 60      # ingatkan kalau penuh dalam 60 menit (0 = saat sudah penuh)
    daily_time: "20:00"      # daily yang belum selesai diingatkan mulai jam ini
    weekly: true
    expeditions: true
  # off_banner: {hsr: [Yunli]}   # lihat "Pity dan 50/50"

notify:
  enabled: true
  topic: cyrene-isi-dengan-topikmu
```

Penjelasan tiap kunci ada di `config.example.yaml`. Lalu cek:

```powershell
waifu check                       # ✓ Cookie HoYoLAB ..., ✓ Riwayat HSR: alamat riwayat ada di cache game
waifu gacha accounts              # akun game di cookie; → = akun yang dipakai (ganti dengan gacha.uids)
waifu gacha status                # stamina, daily, mingguan, ekspedisi, check-in tiap game
waifu gacha checkin               # ambil check-in sekarang (biasanya otomatis)
waifu gacha history hsr --full    # impor riwayat pertama (1100 pull HSR: sekitar 40 detik)
waifu gacha codes                 # kode redeem tiap game: yang belum ditukarkan dan hasil terakhir
waifu gacha report                # laporan 7 hari terakhir tiap game, seperti laporan mingguannya
waifu gacha income                # pendapatan Primogem, Stellar Jade, Polychrome bulan ini (--month 2026-09)
waifu gacha hsr characters        # data akun dari Battle Chronicle (juga genshin dan zzz; endgame, events)
waifu notify-test                 # notifikasi tes ke HP
```

Perintah `waifu gacha` tetap jalan walau `gacha.enabled: false`; yang dinyalakan
`gacha.enabled` adalah tool game untuk otaknya, pengingat, dan laporan mingguan saat
`waifu run`.

### Riwayat pull, pity, dan 50/50

- Cyrene membaca riwayat yang sama dengan halaman riwayat di game. Kuncinya (authkey)
  ditinggalkan game di cache-nya setiap kali kamu membuka **riwayat warp / wish /
  Signal Search** di game, dan berlaku sekitar sehari. Dengan `cookie_token_v2` di
  cookie, dia bisa membuat kuncinya sendiri.
- Jalankan `waifu gacha history <game> --full` sekali per game (`hsr`, `genshin`,
  `zzz`) untuk mengimpor semuanya. Setelah itu hanya pull baru yang dibaca, juga saat
  kamu bertanya lewat suara. Riwayatnya disimpan di `data\gacha\` (tidak di-commit).
- **HoYoverse mencatat pull sekitar satu jam setelah terjadi**, jadi pull yang baru saja
  dilakukan belum terlihat. Cyrene akan bilang begitu.
- Tiap banner punya pity sendiri (termasuk banner kolaborasi HSR dan rerun ZZZ). Hard
  pity: 90 untuk karakter dan standar, 80 untuk light cone, senjata, W-Engine, dan
  Bangboo.
- **Menang atau kalah 50/50** dinilai dari daftar karakter standar: Genshin (Dehya,
  Diluc, Jean, Keqing, Mona, Qiqi, Tighnari, Yumemizuki Mizuki), HSR (Bailu, Bronya,
  Clara, Gepard, Himeko, Welt, Yanqing), ZZZ (Grace, Koleda, Lycaon, Nekomata, Rina,
  Soldier 11). Sejak HSR 4.2 kamu bisa memilih karakter cadangan 50/50 lewat Celestial
  Invitation. Kalau pilihanmu bukan karakter standar, tambahkan namanya di
  `gacha.off_banner.hsr`, supaya Cyrene tidak mengucapkan selamat saat kamu sebenarnya
  kalah.

### Data akun HSR: karakter, endgame, dan event

Selama `hsr` ada di `gacha.games`, Cyrene juga bisa membaca akun **Honkai: Star Rail**
Master dari **Battle Chronicle** HoYoLAB, hanya saat ditanya:

- **Karakter.** Semua karakter yang Master punya: rarity, level, Eidolon, Path, elemen,
  dan Light Cone yang dipakai (level dan Superimposition). Untuk satu karakter juga stat
  akhirnya, main stat relic, dan memosprite.
- **Endgame musim ini.** Memory of Chaos, Pure Fiction, Apocalyptic Shadow, dan Anomaly
  Arbitration: bintang, lantai terdalam, jumlah pertarungan, cycle atau skor lantai
  terbaik, dan kapan musimnya berakhir.
- **Kalender event.** Warp yang sedang jalan dan yang berikutnya dengan 5★ dan 4★
  unggulannya, event yang berjalan beserta progres Master, dan jadwal endgame, masing-masing
  dengan kapan mulai atau berakhir.

```text
Master : "Cyrene, Acheron-ku Eidolon berapa?"
Cyrene : "Acheron-mu E2, Master, dan dia pakai Along the Passing Shore S1."
Master : "Karakter Nihility-ku siapa aja?"
Cyrene : "Ada Acheron, Silver Wolf, dan Pela, Master."
Master : "Memory of Chaos-ku udah berapa bintang?"
Cyrene : "Tiga puluh bintang, Master, sampai Stage 12. Musimnya berakhir empat hari lagi."
Master : "Banner HSR apa sekarang?"
Cyrene : "Sekarang banner Acheron, Master, sampai hari Selasa."
```

- Dia menjawab dari data itu saja. Dia boleh berkomentar, tapi tidak ada saran build
  atau tim dari kode, dan dia tidak pernah bilang berapa bintang lagi yang kurang (datanya
  tidak memuat bintang maksimal). Karakter atau Light Cone yang baru rilis dia akui belum
  dia kenal.
- Trailblazer punya satu karakter per Path (begitu juga March 7th), semuanya dengan nama
  yang sama. Sebut Path-nya untuk satu Path saja: "Trailblazer Harmony-ku pakai Light Cone
  apa?"
- Banner hanya disampaikan sebagai informasi. Dia tidak pernah menyuruh pull atau top-up,
  dan tidak pernah mengingatkan banner atau musim endgame yang mau habis tanpa ditanya,
  juga tidak di sapaan atau briefing pagi.
- Belum ada: substat dan nama set relic, trace, tim dan musim endgame sebelumnya, dan detail
  Simulated Universe (poin mingguannya sudah ada di status). Data akun Genshin dan ZZZ ada di
  bagian berikutnya.
- Jawaban disimpan sebentar di memori supaya pertanyaan beruntun tidak membebani HoYoLAB:
  karakter 15 menit, endgame 10 menit, kalender 60 menit. Eidolon atau Light Cone yang
  baru diganti bisa baru terlihat setelah itu; `waifu gacha hsr` selalu membaca ulang.

Dari terminal (selalu langsung dari HoYoLAB, juga saat `gacha.enabled: false`):

```powershell
waifu gacha hsr characters   # satu baris per karakter: Acheron ★5 Lv80 E2 (Nihility), Along the Passing Shore Lv80 S1
waifu gacha hsr endgame      # tiap mode: bintang, lantai terdalam, berakhir <jam> (<sisa waktu>)
waifu gacha hsr events       # warp, event (progres Master), dan jadwal endgame
```

**Privasi.** Data ini dibaca dengan cookie HoYoLAB yang sama, hanya saat Master bertanya
(tidak pernah dicek berkala), disimpan di memori beberapa menit, dan tidak pernah ditulis
ke disk atau ke log, kecuali dengan `-v`: mode itu mencatat hasil setiap tool (termasuk
data HSR ini) di level DEBUG, ke layar dan ke `--log-file` kalau dipakai. UID tidak pernah
masuk log, juga dengan `-v`. Jawaban untuk otaknya tidak memuat UID, nickname, ID, ikon,
atau deskripsi. Cyrene tidak menyimpannya ke ingatan kecuali Master yang minta.

**Setelan Battle Chronicle.** HoYoLAB hanya memberikan data ini kalau Battle Chronicle HSR
Master boleh dibaca. Setelannya ada di HoYoLAB: **Battle Chronicle** → ikon pengaturan
(tempat yang sama dengan Real-Time Notes di langkah 2), yaitu *Show your Battle Chronicle
on your profile* dan *Show your Character Details in the Battle Chronicle* untuk HSR.
Kalau tertutup, Cyrene bilang "Data HSR ini tidak bisa dibaca karena Battle Chronicle di
HoYoLAB tidak publik" dan di mana setelannya. **Dia tidak pernah mengubah setelan itu
sendiri**: membukanya atau tidak, Master yang memutuskan.

### Data akun Genshin dan ZZZ: karakter, endgame, dan event

Untuk **Genshin Impact** dan **Zenless Zone Zero** yang ada di `gacha.games`, Cyrene juga bisa
membaca akun Master dari **Battle Chronicle** HoYoLAB, sama seperti data HSR: hanya saat
ditanya.

Genshin Impact:

- **Karakter.** Semua karakter yang Master punya: rarity, level, Constellation, elemen, jenis
  weapon, dan weapon yang dipakai (level dan Refinement). Untuk karakter yang ditanya namanya
  juga stat akhirnya, stat weapon, main stat artifact, dan set yang dipakai.
- **Endgame musim ini.** Spiral Abyss (bintang, chamber terdalam seperti 12-3, jumlah
  pertarungan, dan bintang empat lantai tertinggi), Imaginarium Theater (difficulty, act
  terjauh, dan bintang), dan Stygian Onslaught (difficulty, waktu clear, dan waktu tiap
  tantangan), masing-masing dengan kapan musimnya berakhir.
- **Kalender event.** Banner karakter, weapon, dan Chronicled Wish yang sedang jalan dan yang
  berikutnya dengan 5★ dan 4★ unggulannya, event yang berjalan, dan jadwal Spiral Abyss serta
  Imaginarium Theater.

Zenless Zone Zero:

- **Agent.** Semua agent yang Master punya: rank (S atau A), level, Mindscape Cinema, elemen,
  dan specialty. Untuk agent yang ditanya namanya juga W-Engine-nya (level dan Refinement),
  stat akhir, main stat Drive Disc, dan set yang dipakai.
- **Endgame musim ini.** Shiyu Defense (rating, skor dan skor maksimalnya, waktu clear, dan
  rating tiap tahap) dan Deadly Assault (skor, bintang, serta bintang dan skor tiap boss),
  masing-masing dengan kapan musimnya berakhir.
- **Kalender event.** Banner agent dan W-Engine yang sedang jalan dan yang berikutnya dengan
  agent S dan A unggulannya (banner W-Engine tanpa nama, karena HoYoLAB tidak memberikannya),
  dan event yang berjalan beserta Polychrome yang sudah Master dapat dari tiap event dari
  semua yang bisa didapat (`240/300 Polychrome`).

```text
Master : "Cyrene, Furina-ku C berapa, pakai weapon apa?"
Cyrene : "Furina-mu C2, Master, pakai Splendor of Tranquil Waters R1."
Master : "Abyss-ku lantai 12 berapa bintang?"
Cyrene : "Lantai 12 dapat delapan dari sembilan bintang, Master."
Master : "Ellen pakai W-Engine apa?"
Cyrene : "Ellen pakai Deep Sea Visitor, Master, Refinement dua."
Master : "Shiyu Defense-ku rating apa, berakhir kapan?"
Cyrene : "Rating S, Master, skornya lima puluh dua ribu. Musimnya berakhir dua minggu lagi."
Master : "Banner Genshin apa sekarang?"
Cyrene : "Sekarang banner Furina, Master, sampai hari Selasa."
```

- Aturannya sama dengan data HSR: dia menjawab dari data itu saja, tanpa saran build atau tim
  dari kode, dan tidak pernah bilang berapa bintang lagi yang kurang, kecuali per lantai
  Spiral Abyss (satu lantai paling banyak 9 bintang). Karakter, weapon, atau W-Engine yang
  baru rilis dia akui belum dia kenal. Banner hanya disampaikan sebagai informasi, tidak
  pernah sebagai ajakan pull atau top-up, dan tidak pernah tanpa ditanya, juga tidak di
  sapaan atau briefing pagi.
- Dengan lebih dari satu game di `gacha.games`, dia memilih game dari pertanyaanmu (nama
  karakter, mode endgame). Untuk banner dan event, sebut game-nya, juga untuk HSR: "banner
  HSR apa sekarang?", "banner Genshin apa sekarang?", "event ZZZ apa yang mau habis?".
- HoYoLAB hanya memuat satu Traveler, dengan elemen yang sedang dipakai: "Traveler-ku C
  berapa?". Untuk nama panggilan, Cyrene diminta memakai nama di game (Tartaglia untuk
  Childe, Traveler untuk Aether atau Lumine); kalau dia tetap bilang tidak menemukan
  karakternya, sebut nama di game-nya. Agent ZZZ juga bisa disebut dengan nama lengkapnya
  ("Ellen Joe").
- Belum ada: level talent Genshin dan level skill ZZZ, substat artifact dan Drive Disc,
  W-Engine di daftar semua agent (W-Engine hanya ada untuk agent yang ditanya namanya), mode
  ZZZ selain Shiyu Defense dan Deadly Assault, Bangboo, info gacha ZZZ (pity sudah ada dari
  riwayat pull), tim dan musim endgame sebelumnya, detail tiap act Imaginarium Theater, dan
  satu pertanyaan yang mencari di semua game sekaligus.
- Jawaban disimpan sebentar di memori seperti data HSR: karakter dan detailnya 15 menit,
  endgame 10 menit, kalender 60 menit. `waifu gacha genshin` dan `waifu gacha zzz` selalu
  membaca ulang.

Dari terminal (selalu langsung dari HoYoLAB, juga saat `gacha.enabled: false`):

```powershell
waifu gacha genshin characters   # satu baris per karakter: Furina ★5 Lv90 C2 (Hydro, Sword), Splendor of Tranquil Waters Lv90 R1
waifu gacha genshin endgame      # tiap mode: bintang, terdalam, difficulty, detik, berakhir <jam> (<sisa waktu>)
waifu gacha genshin events       # banner, event, dan jadwal endgame
waifu gacha zzz characters       # satu baris per agent: Ellen S Lv60 M2 (Ice, Attack)
waifu gacha zzz endgame          # Shiyu Defense dan Deadly Assault: rating, skor, bintang, berakhir <jam> (<sisa waktu>)
waifu gacha zzz events           # banner dan event
```

Detail satu karakter (stat, artifact, Drive Disc) tidak ada di terminal: tanyakan ke Cyrene
lewat `waifu chat -v`, yang juga mencatat hasil tool-nya di layar.

**Privasi.** Sama dengan data HSR: dibaca hanya saat Master bertanya, disimpan di memori
beberapa menit, dan tidak pernah ditulis ke disk atau ke log, kecuali hasil tool dengan `-v`.
Jawaban untuk otaknya tidak memuat UID, nickname, avatar, ID, ikon, atau deskripsi, termasuk
nickname dan avatar yang ikut dikirim HoYoLAB bersama data Shiyu Defense dan Deadly Assault.

**Setelan Battle Chronicle.** Tiap game punya setelannya sendiri di HoYoLAB: **Battle
Chronicle** → ikon pengaturan, pilih Genshin Impact atau Zenless Zone Zero, yaitu *Show your
Battle Chronicle on your profile* dan sakelar detail karakter di bawahnya (*Show your Character
Details in the Battle Chronicle*; namanya di ZZZ bisa sedikit berbeda). Kalau tertutup, Cyrene
bilang "Data Genshin ini tidak bisa dibaca karena Battle Chronicle di HoYoLAB tidak publik"
(atau "Data ZZZ ini ...") dan di mana setelannya. **Dia tidak pernah mengubah setelan itu
sendiri**: membukanya atau tidak, Master yang memutuskan.

**Daftar nama karakter.** Bacaan Battle Chronicle pertama dalam seminggu (juga untuk HSR) bisa
membuat genshin.py mengunduh daftar nama karakter Genshin dari gi.yatta.moe, atau kalau gagal
dari GitHub (EnkaNetwork), lalu GitLab (AnimeGameData), dan menyimpannya di
`%TEMP%\genshinpy\`. Unduhan itu tidak membawa data Master, tapi bisa membuat bacaan pertama
lebih lambat. Spiral Abyss butuh daftar itu: kalau unduhannya gagal, hanya Spiral Abyss yang
tidak terbaca, mode lain tetap.

**Persona buatan sendiri.** Sejak v1.23 ketiga tool data akun bernama `game_characters`,
`game_endgame`, dan `game_events` (dulu `hsr_characters`, `hsr_endgame`, dan `hsr_events`).
Kedua persona bawaan sudah memakai nama baru. Kalau `assistant.persona_file` menunjuk ke
persona yang kamu ubah sendiri dan masih menyebut `hsr_*`, ganti dengan nama baru (salin baris
game_characters dari `config/persona.md`): nama lama membuat Cyrene salah memanggil tool dulu,
jadi jawabannya lebih lambat.

### Kode redeem

Selama `waifu run` dan teman main gacha menyala, Cyrene memeriksa kode redeem baru untuk
game di `gacha.games` (HSR, Genshin, ZZZ), lalu bilang sendiri dan mengirimnya ke HP kalau
notifikasi menyala. **Kode hanya ditukarkan di akun HoYoLAB Master kalau Master minta atau
setuju**, kecuali `auto_redeem: true`.

```text
Cyrene : "Master, ada 2 kode HSR baru: 60 stellar jade, dan 50 stellar jade, 10k credits,
          two traveler’s guides, and five bottled soda. Mau kutukarkan?"
Master : "Cyrene, iya, tukarkan."
Cyrene : "Satu kode sudah kutukarkan, Master, hadiahnya ada di surat dalam game. Yang satu
          lagi ternyata sudah pernah dipakai."
Master : "Cyrene, pakai kode ABC123XYZ di Genshin."
Master : "Cyrene, ada kode baru nggak?"
```

- **Sumbernya:** API komunitas `hoyo-codes.seria.moe`, yang juga dipakai penukaran kode
  otomatis **Hoyo Buddy** (proyek GPL-3.0). Kodenya dicek ke HoYoLAB dengan akun Asia, dan
  hanya kode yang masih berlaku menurut sumber itu yang dipakai: kode yang tidak lagi
  tercantum di sana (misalnya kode livestream yang sudah kedaluwarsa) tidak lagi ditawarkan
  untuk ditukarkan. Terima kasih kepada pembuatnya. Karena tidak ada ketentuan pemakaian yang
  dipublikasikan untuk pihak lain, Cyrene bertanya sekali sekitar semenit setelah `waifu run`
  siap, lalu paling sering sekali per game setiap `poll_hours` (bawaan 6 jam), ditambah saat
  kamu menjalankan `waifu gacha codes`. Kode yang hanya bisa dipakai lewat game (kode event
  web) tidak termasuk.
- **Pemberitahuan:** sekitar satu menit setelah dia siap, lalu setiap `poll_hours`, kode baru
  tiap game disebut dalam satu kalimat dengan wajah senang: hadiahnya (paling banyak tiga,
  sisanya dihitung), lalu "Mau kutukarkan?". Notifikasi HP memuat setiap kode beserta
  hadiahnya. Pengumuman tidak membuka jendela lanjutan, jadi jawab dengan memanggil namanya:
  "Cyrene, iya, tukarkan." Setiap kode hanya diberitahukan sekali, juga setelah restart. Saat
  pertama kali menyala, semua kode yang masih berlaku ikut diberitahukan sebagai kode baru;
  yang sudah pernah Master pakai akan dia sebut "sudah pernah ditukarkan". Dengan
  `notices: false` dia tetap mengecek kode baru (jadi jawabannya tetap terkini), hanya tidak
  pernah bilang sendiri. `waifu chat` tidak mengecek sendiri: dia tahu kode yang terakhir
  dibaca `waifu run` atau `waifu gacha codes`.
- **Menukarkan:** "iya, tukarkan", "redeem kode HSR yang baru", atau "pakai kode ABC123 di
  Genshin". Kode ditukarkan satu per satu dengan jeda 6 detik (HoYoLAB menolak penukaran yang
  terlalu rapat), yang terbaru dulu, paling banyak 10 sekali minta. **Hadiahnya masuk ke
  surat dalam game.** Kalau HoYoLAB tidak bisa dihubungi, sedang membatasi permintaan, minta
  captcha, atau menolak cookie-nya, sisa kodenya tidak dicoba dulu: minta lagi nanti. Kode
  yang Master sebut sendiri ikut disimpan, tetap bisa dicoba lagi walau tidak tercantum di
  sumbernya, dan tidak pernah diumumkan sebagai kode baru. Dia tidak pernah mengarang kode.
- **`auto_redeem: true`:** kode baru langsung ditukarkan tanpa bertanya, lalu dia bilang
  hasilnya: "Master, aku sudah menukarkan 2 kode HSR baru: ... Hadiahnya ada di surat dalam
  game." Ini bertindak atas akun Master tanpa bertanya dulu, jadi bawaannya mati. Kode yang
  saat itu belum bisa ditukarkan (misalnya HoYoLAB sedang bermasalah) disebut sekali beserta
  alasannya, lalu menunggu sampai Master minta mencoba lagi. Kalau `waifu run` dimatikan saat
  dia sedang menukarkan, dia berhenti sebelum kode berikutnya: kode yang sudah dijawab
  HoYoLAB tidak diumumkan, tapi hasilnya tercatat (lihat `waifu gacha codes`, atau tanya "ada
  kode baru nggak?"), dan sisanya ditukarkan lalu diberitahukan setelah dia menyala lagi.
- **Cookie yang dibutuhkan:** melihat kode tidak butuh cookie. Menukarkan butuh `gacha.cookie`
  yang memuat `cookie_token_v2`, `account_id_v2`, dan `account_mid_v2` (langkah 1 sudah
  menyalin semuanya). `waifu check` menunjukkan apakah `cookie_token_v2` ada, hanya namanya,
  tidak pernah isinya.

Hasil penukaran yang mungkin:

| Hasil | Artinya |
|---|---|
| berhasil ditukarkan | hadiahnya ada di surat dalam game |
| sudah pernah ditukarkan | kode ini sudah dipakai di akun ini |
| sudah kedaluwarsa | masa berlakunya habis |
| tidak valid | HoYoLAB tidak mengenal kodenya |
| tidak berlaku di server akun ini | kodenya untuk server lain |
| butuh level akun yang lebih tinggi | naikkan level akun dulu |
| belum bisa ditukarkan | HoYoLAB sedang membatasi penukaran, cookie tidak bisa dipakai (tanpa `cookie_token_v2` atau sudah kedaluwarsa: ambil ulang, langkah 1), atau masalah lain seperti koneksi; pesannya menyebut yang mana |

Kode yang belum bisa ditukarkan tetap ada di daftar "belum ditukarkan" dan bisa dicoba lagi.

Dari terminal (juga saat `gacha.enabled: false` atau `redeem_codes.enabled: false`):

```powershell
waifu gacha codes               # baca kode terbaru, lalu tiap game: yang belum ditukarkan (baru, atau sejak tanggal) dan 5 hasil terakhir
waifu gacha codes hsr           # satu game saja
waifu gacha codes hsr --redeem  # tukarkan kode HSR yang belum ditukarkan di akun HoYoLAB Master
```

Kode yang baru terbaca di terminal tetap diberitahukan oleh `waifu run` yang sedang berjalan
(juga ke HP), kecuali sudah ditukarkan dengan `--redeem`.

Pengaturan (tidak perlu ditulis untuk memakai bawaannya; contohnya ada di
`config.example.yaml`, dan `waifu run` perlu dijalankan ulang setelah mengubahnya):

```yaml
redeem_codes:
  enabled: true                       # false: tanpa tool dan tanpa pemberitahuan
  poll_hours: 6                       # cek kode baru tiap N jam selama `waifu run` (1-48)
  auto_redeem: false                  # true: kode baru langsung ditukarkan tanpa bertanya
  notices: true                       # false: hanya menjawab kalau ditanya; kode tetap dicek (auto_redeem pun tidak jalan)
  state_path: data/redeem_codes.json
```

`data\redeem_codes.json` hanya ada di PC ini dan tidak di-commit: kode yang dikenal, hadiahnya,
hasil penukaran terakhir, dan mana yang sudah diberitahukan. Kode yang tidak lagi ada di
sumbernya tidak ditawarkan lagi dan dilupakan setelah 60 hari (sampai saat itu, kalau muncul
lagi, kode itu tidak dianggap baru). Seperti file Cyrene lainnya, file yang rusak tidak pernah
ditimpa: namanya diganti menjadi `redeem_codes.json.corrupt-<tanggal>-<jam>` dan dia mulai dari
kosong.

**Privasi.** Ke `hoyo-codes.seria.moe` hanya dikirim nama game, tanpa cookie atau data
tentang Master. Penukaran ke HoYoLAB memakai cookie yang sama dengan bagian lain; cookie dan
UID tidak pernah masuk log atau pesan.

### Laporan game mingguan

Seminggu sekali, bawaannya hari Minggu jam 19.00, selama `waifu run` dan teman main gacha
menyala, Cyrene bercerita sendiri tentang minggu Master di game yang ada di `gacha.games`,
dalam 3-4 kalimat seperti teman yang ikut main. Teks yang sama dikirim ke HP kalau notifikasi
menyala.

```text
(Minggu 19.00)  Cyrene : "Minggu ini Master dapat Castorice di HSR, jatah guaranteed-nya
                          dari minggu lalu! Pity Genshin sekarang 71. Memory of Chaos berakhir
                          Senin dini hari dengan 30 bintang, dan banner Furina juga selesai
                          Selasa."
Master : "Cyrene, gimana minggu ini di game-ku?"
```

Isinya, untuk tiap game:

- **Pull 7 hari terakhir** dari riwayat pull yang tersimpan di `data\gacha\`: jumlahnya per
  banner, dan tiap 5★ (S-rank di ZZZ) dengan berapa pull-nya dan apakah menang 50/50 (dinilai
  dari daftar karakter standar, lihat [Riwayat pull, pity, dan
  50/50](#riwayat-pull-pity-dan-5050)).
- **Pity** tiap banner dan apakah 5★ berikutnya pasti limited.
- **Endgame musim ini** (bintang, lantai, skor) dan musim yang berakhir dalam 7 hari.
- **Banner dan event yang berakhir dalam 7 hari.** Hanya kabar: dia tidak pernah menyarankan
  pull, belanja, atau top up, dan tidak menyebut berapa yang sudah dihabiskan.
- **Kode redeem yang belum ditukarkan**, kalau [kode redeem](#kode-redeem) menyala.
- **Jam main 7 hari terakhir**, kalau [jam main](#jam-main) menyala: berapa lama game itu
  terbuka dan dalam berapa sesi, menurut catatannya sendiri selama `waifu run` menyala.
- **Pendapatan bulan ini sejauh ini**: mata uang premium yang didapat dan kira-kira berapa pull,
  dari catatan bulanan HoYoLAB (lihat [Pendapatan gacha bulanan](#pendapatan-gacha-bulanan)).

Untuk semua game sekaligus, kalau [pemantau internet](#pemantau-internet) menyala: berapa kali
dan berapa lama internet putus dalam 7 hari terakhir, hanya kalau pernah (`waifu gacha report`
menulisnya sesudah daftar game: `Internet putus minggu ini: 2 kali, total 16 menit`).

Kapan dia melapor:

- **Sekali seminggu, di hari dan jamnya:** dari `weekly_report.time` sampai tengah malam di
  hari `weekly_report.day` (jam PC), dicek setiap 30 detik. PC dinyalakan jam 21.00 hari itu:
  dia melapor begitu siap mendengarkan. PC mati seharian di hari itu: minggu itu dilewati, dan
  tidak disusulkan hari Senin, sesudah reset mingguan game (Senin jam 03.00 WIB).
- **Tidak pernah dua kali.** Hari laporan terakhir ditulis ke `data\weekly_report.json`
  sebelum dia mulai, jadi restart di malam yang sama tidak mengulangnya. Untuk mendengarnya
  lagi (misalnya untuk menguji), hapus file itu lalu jalankan ulang `waifu run` di hari dan
  jamnya. File yang rusak dipindah ke `weekly_report.json.corrupt-<waktu>`.
- **Tidak pernah memotong**, sama seperti sapaan pagi: diucapkan di sela kalimat Master,
  sesudah jendela lanjutan tertutup. Sesudahnya jendela lanjutan terbuka, jadi Master bisa
  langsung membalas.
- **Tidak lewat tengah malam.** Kalau laporannya masih tertahan sampai tengah malam (blok fokus,
  obrolan lewat HP, atau Master masih mengobrol dengannya), laporan minggu itu dilewati, tidak
  diucapkan hari Senin. Log `waifu run` mencatatnya.
- **Di sesi obrolan baru**, dan tool keuangan menolak di giliran itu, jadi tidak ada angka
  uang yang terucap atau terkirim ke HP.
- **Riwayatnya diperbarui dulu**, seperti saat Master bertanya pity, tapi hanya untuk game yang
  sudah punya riwayat tersimpan. Game yang belum pernah diimpor tidak diimpor di sini (terlalu
  lama untuk satu giliran): jalankan `waifu gacha history <game> --full` sekali.
- **Minggunya dihitung dari satu jam sebelum jam laporan minggu lalu** (bawaannya Minggu jam
  18.00), seberapa pun telatnya laporan diucapkan. HoYoverse mencatat pull sekitar satu jam
  setelahnya, jadi pull di jam terakhir sebelum laporan belum terhitung malam itu, tapi masuk
  laporan minggu berikutnya; pull yang sudah tercatat di jam itu bisa disebut di dua laporan.
  Di hari lain, saat ditanya atau lewat `waifu gacha report`, minggunya adalah 7 hari dan satu
  jam terakhir.
- **Paling lama 30 detik** untuk semua bacaan. Bagian yang gagal (HoYoLAB tidak bisa
  dihubungi, cookie belum diisi, riwayat di game belum dibuka) atau belum selesai dalam 30
  detik disebut singkat, dan sisanya tetap diceritakan. Kalau tidak ada satu pun yang terbaca,
  dia diam minggu itu dan tidak ada yang dikirim ke HP.
- **Hanya selama `waifu run`.** `waifu chat` punya data yang sama untuk ditanya, tapi tidak
  melapor sendiri.

Dari terminal (juga saat `gacha.enabled: false` atau `weekly_report.enabled: false`), tanpa
otak:

```powershell
waifu gacha report   # 7 hari terakhir tiap game; exit code 1 kalau ada bagian yang tidak terbaca
```

```text
Laporan game 7 hari terakhir, sejak Minggu, 11-10-2026 18:00
HSR (Honkai: Star Rail)
  Pull minggu ini: 36 (Character Event Warp 35, Light Cone Event Warp 1)
  5★ minggu ini: Castorice (30 pull)
  Character Event Warp: pity 5/90, berikutnya 50/50, total 95 pull
  Light Cone Event Warp: pity 1/80, total 1 pull
  Memory of Chaos: 30 bintang (+2 ekstra), lantai terdalam Stage 12, segera berakhir 2026-10-19 03:00 (8 jam lagi)
  Pure Fiction: belum dimainkan musim ini, berakhir 2026-11-02 03:00 (14 hari 8 jam lagi)
  Apocalyptic Shadow: belum dimainkan musim ini, berakhir 2026-11-16 03:00 (28 hari 8 jam lagi)
  Anomaly Arbitration: belum dimainkan musim ini
  Berakhir dalam 7 hari:
    Bloom of Death (karakter): 5★ Castorice; 4★ Pela, Lynx; berakhir 2026-10-21 14:00 (2 hari 19 jam lagi)
    Planar Fissure: 3/7, berakhir 2026-10-19 03:00 (8 jam lagi)
  Kode belum ditukarkan: tidak ada
  Jam main minggu ini: 9 jam 40 menit dalam 6 sesi
  Pendapatan bulan ini: 4.800 Stellar Jade (30 pull), hari ini 60
Genshin (Genshin Impact)
  Pull minggu ini: 12 (Character Event Wish 12)
  Character Event Wish: pity 71/90, berikutnya 50/50, total 131 pull
  Spiral Abyss: 36 bintang, terdalam 12-3, berakhir 2026-11-16 03:00 (28 hari 8 jam lagi)
  Imaginarium Theater: 10 bintang, difficulty Visionary, berakhir 2026-11-01 03:00 (13 hari 8 jam lagi)
  Stygian Onslaught: belum dimainkan musim ini
  Berakhir dalam 7 hari:
    Banner karakter: 5★ Furina; 4★ Charlotte, Chongyun, Kirara; berakhir 2026-10-20 17:00 (1 hari 21 jam lagi)
  Kode belum ditukarkan: tidak ada
  Jam main minggu ini: 2 jam 15 menit dalam 3 sesi
  Pendapatan bulan ini: 5.400 Primogem (33 pull), hari ini 120
ZZZ (Zenless Zone Zero)
  ✗ Belum ada riwayat ZZZ yang tersimpan; jalankan 'waifu gacha history zzz --full' sekali.
  ...
Catatan: HoYoverse mencatat pull sekitar satu jam setelahnya, jadi pull barusan mungkin belum ada.
```

Daftar kode diperbarui oleh `waifu run` saat teman main gacha menyala. Dengan `gacha.enabled:
false` tidak ada yang memperbaruinya selain `waifu gacha codes`, jadi barisnya berbunyi `Kode
belum ditukarkan (daftar tersimpan, perbarui dengan 'waifu gacha codes'): ...`.

`waifu check` menampilkan `✓ Laporan game mingguan: tiap Minggu jam 19:00` (dengan
`, juga ke HP` kalau notifikasi menyala), `! Laporan game mingguan: ...; cookie HoYoLAB belum
diisi (gacha.cookie), jadi endgame dan event tidak terbaca`, atau `! Laporan game mingguan
mati (...); 'waifu gacha report' tetap bisa dipakai`, tanpa menghubungi HoYoLAB.

Pengaturan (tidak perlu ditulis untuk memakai bawaannya; contohnya ada di
`config.example.yaml`, dan `waifu run` perlu dijalankan ulang setelah mengubahnya):

```yaml
weekly_report:
  enabled: true                       # false: tanpa laporan sendiri dan tanpa tool-nya
  day: sunday                         # monday, tuesday, wednesday, thursday, friday, saturday, atau sunday
  time: "19:00"                       # jam PC; 19.00 WIB = 20.00 server Asia, sebelum reset mingguan
  push: true                          # kirim laporannya juga ke HP (kalau notify menyala)
  state_path: data/weekly_report.json
```

**Privasi.** Angka minggu itu (pull, pity, endgame, kode, jam main, pendapatan bulan ini,
internet putus)
dikirim ke otaknya (dengan Claude, ke API Anthropic, sama seperti saat Master bertanya pity) dan
tidak pernah ditulis ke log biasa. Kalimat laporannya ke `ntfy.sh` kalau notifikasi HP dan
`weekly_report.push` menyala. `data\weekly_report.json` hanya menyimpan tanggal laporan terakhir.

### Pendapatan gacha bulanan

HoYoLAB mencatat apa yang Master dapat tiap bulan di tiap game: **Traveler's Diary** (Genshin),
**Trailblaze Monthly Calendar** (HSR), dan **Inter-Knot monthly report** (ZZZ). Cyrene
membacanya saat ditanya, sebagai bahan untuk merencanakan pull.

```text
Master : "Cyrene, bulan ini aku dapat berapa Primogem?"
Cyrene : "Bulan ini Master sudah dapat sekitar lima ribu empat ratus Primogem, kira-kira tiga
          puluh tiga pull, paling banyak dari Daily Activity."
Master : "Stellar Jade bulan lalu berapa?"
Master : "Pendapatan gacha bulan ini gimana?"      (semua game di gacha.games)
```

Yang dibaca, per game:

| Game | Mata uang premium | Lainnya | Bulan sebelumnya | Hari ini |
|---|---|---|---|---|
| Genshin | Primogem | Mora | ya, dengan persentasenya | ya |
| HSR | Stellar Jade | Star Rail Pass | ya, dengan persentasenya | ya, juga kemarin |
| ZZZ | Polychrome | Master Tape, Boopon | tidak ada | tidak ada |

- **Pull** dihitung 160 per pull dari mata uang premium saja. Pass dan tape yang didapat disebut
  terpisah dan tidak ditambahkan (HoYoLAB juga tidak bilang apakah Star Rail Pass-nya Special
  atau biasa).
- **Sumber**: dari mana mata uang premium itu datang (Daily Activity, Events, Spiral Abyss,
  Memory of Chaos, Shiyu Defense, ...), yang terbanyak dulu.
- **Bulan sebelumnya** seperti yang dihitung HoYoLAB; bulan yang masih berjalan biasanya masih di
  bawahnya, jadi persentase minus di tengah bulan itu wajar.
- **Hanya bulan ini dan dua bulan sebelumnya**, karena hanya itu yang disimpan HoYoLAB. Bulan
  dihitung dengan jam server Asia, jadi bulan baru mulai jam 23.00 WIB di hari terakhir bulan.
  Bulan lain ditolak tanpa bertanya ke HoYoLAB, dan jawaban HoYoLAB untuk bulan lain dari yang
  diminta tidak pernah disebut sebagai bulan itu.
- **Hanya kalau ditanya.** Tidak disebut di sapaan pagi atau hal lain yang dia mulai sendiri,
  kecuali satu bagian singkat di [laporan game mingguan](#laporan-game-mingguan). Angkanya
  informasi untuk merencanakan pull: dia tidak pernah menyarankan pull, belanja, atau top-up, dan
  tidak menilai banyak sedikitnya.
- Catatan HoYoLAB bisa **tertinggal** dari game. Yang sudah dibaca disimpan di memori 15 menit,
  jadi pertanyaan berikutnya dalam waktu itu tidak bertanya ke HoYoLAB lagi; angka hari ini
  dibaca ulang setelah tengah malam server.

Dari terminal (juga saat `gacha.enabled: false`), tanpa otak:

```powershell
waifu gacha income                      # bulan ini, semua game di gacha.games
waifu gacha income zzz --month 2026-09  # satu game, salah satu dari dua bulan sebelumnya
```

```text
Pendapatan gacha Oktober 2026, sampai sekarang:
HSR (Honkai: Star Rail)
  Stellar Jade: 4.800 (30 pull), hari ini 60, kemarin 120
  Bulan sebelumnya: 10.000; bulan ini -52% dibanding itu
  Sumber: Daily Activity 1.800 (38%), Events 1.200 (25%), Memory of Chaos 1.000 (21%), Mail 800 (16%)
  Lainnya: Star Rail Pass 5
Genshin (Genshin Impact)
  Primogem: 5.400 (33 pull), hari ini 120
  Bulan sebelumnya: 9.000; bulan ini -40% dibanding itu
  Sumber: Daily Activity 1.800 (33%), Events 1.600 (30%), Spiral Abyss 1.200 (22%), Mail 800 (15%)
  Lainnya: Mora 1.234.567
ZZZ (Zenless Zone Zero)
  Polychrome: 3.200 (20 pull)
  Sumber: Daily Activity 1.200 (38%), Events 1.000 (31%), Shiyu Defense 600 (19%), Growth 400 (12%)
  Lainnya: Encrypted Master Tape 3, Boopon 12
Catatan: pull dihitung 160 per pull dari mata uang premium saja, tanpa pass atau tape; catatan bulanan HoYoLAB bisa tertinggal dari game.
```

Exit code 1 kalau ada game yang tidak terbaca (sisanya tetap ditampilkan); bulan di luar tiga
bulan itu ditolak dengan exit code 2. Tidak ada baris `waifu check` tersendiri: cookie-nya sudah
dicek di baris cookie HoYoLAB.

**Privasi.** Dibaca hanya saat Master bertanya (dan untuk laporan mingguan), disimpan di memori
15 menit, dan tidak pernah ditulis ke disk atau ke log biasa. Dengan Claude, angkanya dikirim ke
API Anthropic sebagai hasil tool, sama seperti data akun game lainnya. Jawaban untuk otaknya dan
keluaran terminal tidak memuat UID, nickname, atau avatar.

### Pengingat

| Pengingat | Kapan | Berapa kali | Mengatur |
|---|---|---|---|
| Stamina | penuh dalam `stamina_minutes` menit, atau sudah penuh | sekali, sampai staminanya dipakai lagi atau Cyrene dijalankan ulang | `stamina_minutes` (0 = saat sudah penuh) |
| Daily | mulai `daily_time` kalau daily belum selesai | sekali per hari game (reset 03.00 WIB) | `daily_time` |
| Mingguan | hari Minggu mulai `daily_time` kalau masih ada sisa | sekali per minggu | `weekly: false` |
| Ekspedisi | semua ekspedisi HSR / Genshin selesai | sekali per gelombang (atau setelah Cyrene dijalankan ulang) | `expeditions: false` |
| Check-in | diambil sendiri; dia bilang hadiahnya | sekali per hari (berganti jam 23.00 WIB) | `checkin: false` |
| Masalah | cookie tidak berlaku, captcha, Real-Time Notes mati | sekali per hari | — |
| Musim endgame mau habis | mulai `daily_time`, musim Memory of Chaos, Pure Fiction, Apocalyptic Shadow, Anomaly Arbitration (HSR), atau Spiral Abyss (Genshin) yang berakhir dalam 72 jam dan belum maksimal | sekali per musim | `planner: false` |
| Event mau habis | mulai `daily_time`, event HSR atau ZZZ yang berakhir dalam 48 jam dan progresnya belum penuh (paling banyak 2, yang paling cepat habis dulu) | sekali per hari game, satu kalimat per game bersama musim endgame | `planner: false` |
| Stamina penuh sebelum bangun | kamu menutup game antara 22.00 dan 03.00, dan staminanya penuh sebelum pagi (`briefing.time`, bawaan 07.00) | sekali per malam | `planner: false` |

HoYoLAB dicek tiap `poll_minutes` (bawaan 15). Pengingat daily, mingguan, masalah, dan
check-in disimpan di `data\gacha\reminders.json`, jadi Cyrene yang di-restart tidak
mengulangnya. Stamina dan ekspedisi yang masih penuh disampaikan sekali lagi setelah
restart, karena selama PC mati dia tidak tahu apakah sempat kamu pakai. Tidak ada jam
tenang: dia juga bicara di malam hari selama `waifu run` jalan. Pengingat stamina, daily,
mingguan, ekspedisi, dan tiga pengingat "sebelum reset" yang ditahan [Mode game](#mode-game)
sampai pengecekan berikutnya tidak diucapkan lagi, karena game yang dimainkan mungkin sudah
mengubah isinya (resin sudah dipakai, daily sudah selesai); notifikasi HP-nya, kalau menyala,
sudah terkirim. Blok fokus tanpa main game menahannya sampai istirahat seperti biasa, dan
masalah serta check-in tetap diucapkan.

**Sebelum reset.** Tiga baris terakhir tabel di atas menyala tanpa diatur (`planner: true`):

> "Master, Memory of Chaos di HSR berakhir sekitar 50 jam lagi, baru dapat 30 dari 36. Event
> Gift of Odyssey berakhir sekitar 20 jam lagi, baru 3 dari 7."
>
> "Master, Trailblaze Power di HSR penuh jam 04.10, sebelum kamu bangun."

- Untuk musim dan event, Cyrene membaca kalender event tiap game sekali sehari game, mulai
  `daily_time`, sama seperti `waifu gacha <game> events`: kira-kira 4 permintaan HoYoLAB sehari
  untuk ketiga game, di samping sekitar 576 untuk stamina dan check-in. Bacaan itu disimpan
  sejam bersama jawaban "event apa yang mau habis?", jadi pertanyaanmu sesudahnya tidak membaca
  HoYoLAB lagi. Kalau Battle Chronicle game itu tidak publik, dia mencoba lagi besok tanpa
  bilang apa-apa.
- Hanya yang dicatat HoYoLAB yang disebut: event Genshin, Imaginarium Theater, Stygian
  Onslaught, dan endgame ZZZ (Shiyu Defense, Deadly Assault) tidak punya angka progres di
  kalender, dan musim yang belum kamu mainkan belum punya nilai maksimal, jadi semuanya tidak
  disebut. "Baru dapat 30 dari 36" di HSR adalah angka progres HoYoLAB; cocokkan sekali dengan
  bintang di game (checklist di bawah). Event ZZZ menyebut Polychrome-nya.
- Stamina penuh sebelum bangun butuh [Jam main](#jam-main) menyala (bawaan), supaya dia tahu
  kapan kamu menutup game. Dia bilang di pengecekan HoYoLAB berikutnya (paling lama
  `poll_minutes`) setelah semua game tertutup: game yang kamu tutup selagi game lain masih
  terbuka, atau yang kamu buka lagi, menunggu sampai kamu selesai main. Lalu pengingat stamina
  game itu menunggu sampai pagi, supaya tidak berbunyi jam 04.10 di kamar kosong; selama kamu
  membuka game lagi, pengingat itu berbunyi seperti biasa. Sesudah jam pagi, kalau staminanya
  masih penuh, dia bilang sekali seperti biasa. Jam pagi adalah `briefing.time`, juga saat
  sapaan pagi dimatikan. Kalau staminanya akan penuh dalam `stamina_minutes` (bawaan 60 menit),
  kalimat ini tidak ada: pengingat stamina biasa ("penuh sekitar 25 menit lagi") yang bilang,
  dan tidak ditahan. Game yang tertutup karena PC tidur atau `waifu run` mati lama tidak
  dihitung.
- Selama [mode tamu](#mode-tamu) menyala, ketiganya diam dan tidak membaca HoYoLAB; game yang
  kamu tutup malam itu menunggu sampai tamunya pergi (sebelum pagi). Pengingat lain di tabel
  ini tetap berbunyi seperti dulu.

### Ketentuan dan risiko

- Cyrene hanya **membaca** data dan mengambil check-in harian yang memang disediakan
  HoYoLAB. Dia tidak pernah memainkan game: macro atau auto-farm melanggar ketentuan
  HoYoverse dan bisa membuat akun diblokir.
- `genshin.py` adalah pustaka HoYoLAB **tidak resmi**. Check-in otomatis lewat API
  seperti ini dipakai banyak bot, tapi tidak dijamin 100% aman. Kalau ragu, pasang
  `checkin: false`.
- Kalau HoYoverse mengubah API-nya, Cyrene akan bilang data tidak bisa dibaca;
  `pip install -U genshin` di venv biasanya memperbaikinya.
- Penukaran kode redeem memakai halaman penukaran web yang sama dengan situs resmi, lewat
  `genshin.py` yang tidak resmi, dan hanya saat Master minta (atau dengan `auto_redeem`).

### Pemecahan masalah gacha

- **"Cookie HoYoLAB tidak berlaku lagi".** Ambil ulang cookie (langkah 1).
- **"Real-Time Notes ... belum aktif".** Langkah 2.
- **"Data HSR ini tidak bisa dibaca karena Battle Chronicle di HoYoLAB tidak publik"**
  (atau Data Genshin, Data ZZZ). Lihat "Setelan Battle Chronicle" di bagian Data akun HSR
  atau Data akun Genshin dan ZZZ.
- **Hanya Spiral Abyss yang "tidak bisa dibaca saat ini".** genshin.py belum berhasil
  mengunduh daftar nama karakter (lihat "Daftar nama karakter" di bagian Data akun Genshin
  dan ZZZ). Coba lagi beberapa saat lagi; mode lain tidak terpengaruh.
- **"HoYoLAB minta verifikasi captcha".** Buka HoYoLAB di browser sekali; untuk
  check-in, ambil manual dulu di hari itu.
- **"Buka riwayat warp di HSR sekali, lalu tanya lagi".** Buka halaman riwayat di game
  (banner mana saja), lalu tanya lagi. Agar tidak perlu, tambahkan `cookie_token_v2`.
- **"Riwayat ... sedang dikunci game".** Tutup game sebentar, lalu coba lagi.
- **"Server riwayat ... minta pelan-pelan".** Tunggu semenit.
- **Pity berbeda dengan di game.** Riwayatnya mungkin baru terbaca sebagian: jalankan
  `waifu gacha history <game> --full`.
- **"Cookie HoYoLAB tidak bisa dipakai untuk menukarkan kode".** Penukaran butuh
  `cookie_token_v2` (dengan `account_id_v2` dan `account_mid_v2`): ambil ulang cookie-nya
  (langkah 1), lalu minta lagi.
- **"HoYoLAB menjawab bulan lain untuk pendapatan ...".** HoYoLAB mengirim catatan bulan lain
  daripada yang ditanya, jadi angkanya sengaja tidak disebut. Di pergantian bulan, coba lagi
  sebentar lagi. Kalau terus muncul untuk bulan sebelumnya di ZZZ, mungkin format bulan ZZZ
  yang belum pernah dicoba: catat dan laporkan.
- **"Kode redeem tidak bisa diambil".** `hoyo-codes.seria.moe` tidak bisa dihubungi; `waifu
  run` mencoba lagi di pemeriksaan berikutnya, dan `waifu gacha codes` tetap menampilkan kode
  yang tersimpan.

## Ingatan Cyrene

Isi obrolan dilupakan setiap kali sesi berakhir (10 menit tanpa percakapan atau 40
giliran), tapi hal penting tentang Master tetap diingat: Cyrene menyimpannya sebagai
catatan pendek di `data\memory.json`, dan di awal setiap sesi semua catatan itu dibaca
lagi oleh otaknya. Ingatan sudah menyala dari awal (`memory.enabled: true`), di
`waifu run` dan `waifu chat` (juga dengan `--dry-run`, yang memalsukan perangkat tapi
memakai ingatan yang asli).

```text
Master : "Cyrene, ingat ya, aku alergi udang."
Cyrene : "Siap, aku ingat. Master alergi udang."
(beberapa hari kemudian)
Master : "Cyrene, enaknya masak apa malam ini?"
Cyrene : "Gimana kalau ayam bakar kecap, Master? Pokoknya tanpa udang."
Master : "Cyrene, apa yang kamu ingat tentang aku?"
Cyrene : "Master alergi udang, suka kopi susu, dan Jumat ini ada ujian. がんばって!"
Master : "Cyrene, lupakan soal udang."
Cyrene : "Sudah kuhapus, Master."
```

### Yang dia ingat dan yang tidak

- **Disimpan:** apa saja yang Master minta untuk diingat, dan hal yang awet yang Master
  sebut sambil lalu: suka dan tidak suka, alergi dan kesehatan, rencana dengan tanggalnya
  ("Jumat ada ujian"), orang dan hewan peliharaan, kebiasaan, game dan karakter favorit.
  Satu catatan berisi satu fakta, ditulis seperti `Master alergi udang.`
- **Tidak disimpan:** obrolan ringan, perintah perangkat, dan hal yang sudah ada di
  catatannya. Kalau sebuah fakta berubah ("sekarang aku sudah tidak kerja di sana"),
  catatan lamanya diperbarui, bukan ditambah catatan kedua yang bertentangan.
- **Rahasia tidak pernah disimpan.** Catatan yang berisi kata sandi, PIN, OTP, token,
  cookie, API key, atau nomor kartu ditolak oleh kode Cyrene sendiri, apa pun permintaan
  modelnya, juga kalau ditulis "sandinya", "PIN-ku", atau nomor kartu yang disebut bersama
  tanggal kedaluwarsanya. Yang ditolak adalah kata rahasia yang diikuti isinya dalam empat
  kata berikutnya: tanda `:` atau `=` lalu apa saja ("password wifi: hijau123", juga kalau
  isinya baru muncul setelah kata keempat), angka 4 digit atau lebih ("PIN ATM 1234", "kode
  OTP 482910", juga yang ditulis berkelompok seperti "OTP 482 910" atau "PIN 12-34"), atau
  kata 6 karakter atau lebih yang mencampur huruf dengan angka atau simbol ("passwordnya
  hijau123"). Kata yang menghubungkan rahasia dengan Master ("Master", "milik", "punya",
  "adalah", "yaitu", "yang") tidak ikut dihitung, jadi "PIN ATM BCA milik Master adalah
  123456" juga ditolak. Untuk token listrik atau token PLN, hanya 20 digit tokennya yang
  dianggap isi: daya ("token listrik 1300 VA"), nomor meter atau ID pelanggan, dan nominal
  tetap disimpan. Menyebut rahasianya saja tanpa isinya boleh: "Master harus ganti PIN ATM"
  tetap disimpan, begitu juga kata biasa yang kebetulan mirip, seperti nama Sandi, sandi
  Morse, token listrik, pin lokasi, atau kue cookie. Sandi yang hanya berupa huruf tanpa `:`
  ("password laptop mochi"), atau angka pendek yang ditulis jauh dari kata rahasianya, tidak
  bisa dibedakan dari kata biasa, jadi jangan titipkan rahasia kepadanya.
- Dia tidak merangkum setiap obrolan: yang disimpan hanya yang dipilih otaknya. Kalau
  sesuatu penting, minta saja: "ingat ya, ...".

Catatan baru langsung diketahui di sesi yang sedang berjalan (dia sendiri yang baru
menyimpannya) dan masuk ke daftar ingatannya mulai sesi berikutnya. Kalau jawabannya gagal
setelah catatan tersimpan atau terhapus (misalnya koneksi putus), sesi itu diakhiri, jadi
giliran berikutnya sudah membaca catatan yang terbaru. Batasnya 100 catatan,
masing-masing paling panjang 200 karakter (`memory.max_facts`, `memory.max_chars`); kalau
penuh, dia minta Master menggabungkan atau menghapus catatan lama dulu.

Contoh kalimat saat `waifu run`:

- "Cyrene, ingat ya, aku alergi udang."
- "Cyrene, ingat, tanggal 16 aku ujian."
- "Cyrene, apa yang kamu ingat tentang aku?" (dijawab dengan ringkasan singkat, bukan
  daftar yang dibacakan satu per satu)
- "Cyrene, lupakan soal udang."

### Mengatur catatan dari terminal

```powershell
waifu memory list                               # m3  12-10-2026  Master alergi udang.
waifu memory add "Master suka kopi susu, tanpa gula."
waifu memory forget m3                          # hapus satu catatan (id dari list)
waifu memory clear                              # hanya menghitung yang akan dihapus
waifu memory clear --yes                        # hapus semua catatan
```

Tanggal di `list` adalah kapan catatan itu terakhir diubah. Aturan yang sama berlaku
seperti lewat suara (rahasia, panjang, jumlah); kalau ditolak, perintahnya menampilkan
`✗` dengan alasannya. Perintah ini tetap jalan walau `memory.enabled: false`. Cyrene yang
sedang berjalan melihat perubahan dari terminal mulai sesi obrolan berikutnya.
`waifu check` menampilkan `✓ Ingatan: <n> catatan (data\memory.json)`.

### File data\memory.json

Isinya JSON UTF-8 yang bisa dibuka dengan Notepad:

```json
{
  "version": 1,
  "next_id": 4,
  "facts": [
    {
      "id": "m3",
      "text": "Master alergi udang.",
      "created": "2026-10-12T08:30",
      "updated": "2026-10-12T08:30"
    }
  ]
}
```

- **Edit hanya saat Cyrene mati** (tutup dulu jendela `waifu run`). Kalau dia menyimpan
  catatan selagi file itu terbuka di editor, simpanan editor menimpa catatannya: di antara
  dua program, yang terakhir menulis yang menang. `waifu memory` membaca file tepat
  sebelum mengubahnya, jadi aman dipakai selagi dia berjalan (kecuali keduanya menulis
  pada saat yang persis sama).
- `id` berbentuk `m` lalu angka dan tidak boleh kembar; id yang pernah dipakai tidak
  diberikan lagi. Waktu ditulis seperti `2026-10-12T08:30`. Catatan yang ditulis tangan
  tidak dicek seperti lewat suara, jadi jangan menulis rahasia di sini juga.
- **File yang rusak tidak pernah ditimpa.** Kalau isinya bukan JSON yang benar atau
  bentuknya lain dari contoh di atas, Cyrene mengganti namanya menjadi
  `memory.json.corrupt-<tanggal>-<jam>` (misalnya `memory.json.corrupt-20261013-214500`),
  menulis error di log, lalu mulai dengan ingatan kosong. Selama file itu masih ada,
  `waifu check` menunjukkannya dengan `!`. Untuk memulihkan: matikan Cyrene, perbaiki
  file `.corrupt-...` itu (misalnya koma atau tanda kutip yang hilang), lalu ganti
  namanya kembali menjadi `memory.json`. Kalau sementara itu dia sudah menyimpan catatan
  baru, lihat dulu dengan `waifu memory list`, kembalikan file yang sudah diperbaiki,
  lalu tambahkan lagi catatan baru itu dengan `waifu memory add`.

### Privasi ingatan

- Catatan hanya ada di `data\memory.json` di PC ini. Seluruh `data\` ada di
  `.gitignore`, jadi tidak pernah di-commit.
- Dengan `brain.backend: claude`, semua catatan ikut dikirim ke API Anthropic sebagai
  bagian dari system prompt di setiap sesi, sama seperti isi obrolannya. Dengan Ollama,
  daftar catatannya tidak pernah keluar dari PC (jawaban yang menyebut isinya tetap
  dijadikan suara oleh edge-tts kalau engine `edge` dipakai, seperti jawaban lainnya).
- Catatan yang dia simpan lewat suara juga tercatat di log (`Tool remember(...)`), seperti
  setiap kalimat yang dia dengar.
- Untuk mematikan ingatan: `memory.enabled: false`. Dia tidak lagi membaca atau
  menyimpan catatan, tapi filenya tidak dihapus; hapus isinya dengan
  `waifu memory clear --yes` kalau mau.

## Jadwal, pengingat, dan rutinitas

Cyrene bisa mengingatkan Master pada jam tertentu, menjalankan perintah perangkat pada
jamnya, dan menjalankan **rutinitas**: beberapa perintah perangkat sekaligus yang Master
tulis sendiri di `config.yaml`. Jadwal sudah menyala dari awal (`schedule.enabled: true`)
di `waifu run` dan `waifu chat`.

```text
Master : "Cyrene, ingatkan aku jam 3 sore minum obat."
Cyrene : "Siap, jam tiga sore nanti aku ingatkan, Master."
(15.00)  Cyrene : "Master, sudah jam tiga, waktunya minum obat!"
Master : "Cyrene, matikan AC jam 2 pagi."
Cyrene : "Oke, AC kumatikan jam dua pagi. Selamat tidur, Master."
Master : "Cyrene, mode tidur."
Cyrene : "Lampu kamar sudah mati dan AC di dua puluh enam derajat. おやすみなさい, Master."
```

### Contoh kalimat

- **Pengingat pada jam tertentu:** "Cyrene, ingatkan aku jam 3 sore minum obat."
- **Pengingat N menit lagi:** "Cyrene, 10 menit lagi ingatkan aku angkat jemuran." Dia tahu
  jam sekarang dari setiap kalimatmu, lalu menghitung jamnya sendiri.
- **Berulang:** "Cyrene, setiap hari jam 7 pagi ingatkan aku minum vitamin.", "... tiap hari
  kerja" (Senin–Jumat), atau "... tiap akhir pekan" (Sabtu–Minggu).
- **Dibangunkan:** "Cyrene, bangunkan aku jam 6" menjadi alarm, yang diulang makin keras sampai
  Master bangun: lihat [Alarm bangun](#alarm-bangun).
- **Mingguan, bulanan, tahunan:** "Cyrene, tiap Senin jam 7 ingatkan buang sampah.",
  "Cyrene, tiap tanggal 20 ingatkan bayar listrik.", "Cyrene, ingatkan ulang tahun Ibu tiap
  5 Mei." Tanpa jam, ulang tahun dan tagihan diingatkan jam 08.00 pagi itu ("Master, hari
  ini ulang tahun Ibu!"); sebut jamnya kalau mau lain. Lihat
  [Mingguan, bulanan, dan tahunan](#mingguan-bulanan-dan-tahunan).
- **Jadwal bulan ini:** "Cyrene, jadwal bulan ini apa?", "Ulang tahun siapa bulan ini?"
- **Perangkat pada jamnya:** "Cyrene, matikan AC jam 2 pagi.", "Cyrene, nyalakan lampu kamar
  setiap hari jam 6 sore."
- **Rutinitas sekarang:** "Cyrene, mode tidur." **Rutinitas pada jamnya:** "Cyrene, jalankan
  mode tidur jam 11 malam." Langkah rutinitas juga bisa ditunda, misalnya AC mati tiga jam
  kemudian (lihat [Mode tidur](#mode-tidur)).
- **Daftar jadwal:** "Cyrene, ada jadwal apa saja?" (dijawab dengan ringkasan singkat).
- **Membatalkan:** "Cyrene, batalkan pengingat obat.", "Cyrene, batalkan AC mati nanti."

Jam yang sudah lewat hari ini biasanya berarti besok: "jam 7 pagi" yang diucapkan malam hari
dijadwalkan besok pagi. Dia selalu menyebut jam yang dia jadwalkan; kalau salah, minta
batalkan lalu ulangi.

### Mingguan, bulanan, dan tahunan

```text
Master : "Cyrene, ingatkan ulang tahun Ibu tiap 5 Mei."
Cyrene : "Siap, tiap 5 Mei jam delapan pagi aku ingatkan, mulai 5 Mei tahun depan."
(5 Mei, 08.00)  Cyrene : "Master, hari ini ulang tahun Ibu!"
Master : "Cyrene, jadwal bulan ini apa?"
Cyrene : "Tanggal 20 bayar listrik, dan tiap Senin buang sampah."
```

- **Mingguan** jalan di hari yang sama setiap minggu: hari dari jam pertama yang dia
  jadwalkan.
- **Bulanan** jalan di tanggal yang sama setiap bulan. Tanggal 29, 30, atau 31 jalan di hari
  terakhir bulan yang lebih pendek, lalu kembali ke tanggalnya bulan berikutnya: "tiap tanggal
  31" jalan 30 November, 31 Desember, 31 Januari, 28 Februari (29 di tahun kabisat), 31 Maret,
  dan seterusnya.
- **Tahunan** jalan di tanggal dan bulan yang sama setiap tahun. Ulang tahun 29 Februari
  diingatkan 28 Februari di tahun biasa dan 29 Februari di tahun kabisat.
- Tanggal yang sudah lewat tidak ditolak: "tiap 5 Mei" yang diucapkan bulan Oktober mulai 5 Mei
  tahun depan, dan "tiap tanggal 5" yang diucapkan tanggal 20 mulai bulan depan. Dia selalu
  menyebut kapan jadwal itu pertama kali jalan.
- "Tiap tanggal 31" yang diucapkan di bulan tanpa tanggal 31 (misalnya November) dimulai 31
  Desember. Kalau dia malah menyebut tanggal 30, jadwalnya menjadi tiap tanggal 30: minta
  batalkan lalu ulangi ("tiap tanggal 31, mulai Desember").
- Jadwal tahunan selalu mulai di tanggal terdekatnya, jadi "mulai tahun depan" untuk tanggal
  yang belum lewat tahun ini belum bisa.
- Ulang tahun dan tagihan ikut dihitung dalam batas `schedule.max_entries` (bawaan 50, paling
  banyak 200).
- Pengingat bulanan atau tahunan yang terlewat karena PC mati tetap diucapkan begitu Cyrene
  jalan lagi (lihat [Cara jadwal dijalankan](#cara-jadwal-dijalankan)).

### Cara jadwal dijalankan

- **Pengingat** diucapkan sendiri di sela kalimatmu (tidak pernah memotong jawaban atau
  omonganmu, sama seperti pengingat game) dan dikirim ke HP lewat ntfy kalau
  `notify.enabled: true` (lihat [Notifikasi ke HP](#3-notifikasi-ke-hp-ntfy)).
- **Perintah perangkat dan rutinitas** dijalankan diam-diam, karena Master mungkin sedang
  tidur. Dia hanya bicara kalau gagal, misalnya "Master, jadwal ac off jam 02.00 gagal:
  ...", dan mengirim notifikasi hanya kalau `notify.enabled: true`. Tanpa notifikasi HP,
  kegagalan tengah malam hanya diucapkan sekali ke kamar yang sedang tidur; jadwal sekali
  lalu hilang dari `waifu schedule list`, dan jejak lainnya hanya baris WARNING (bahasa
  Inggris) di log (`logs\waifu.log` saat autostart). Untuk jadwal perangkat dan rutinitas,
  sebaiknya nyalakan [notifikasi ke HP](#3-notifikasi-ke-hp-ntfy).
- **Hanya selama `waifu run` menyala.** Tidak ada hub atau layanan lain yang menjalankan
  jadwal: kalau PC mati, tidur, atau Cyrene ditutup, tidak ada yang jalan. Saat dia jalan
  lagi, jadwal yang terlewat paling lama `schedule.late_grace_minutes` menit (bawaan 30)
  masih dijalankan. Yang lebih telat dilewati: jadwal sekali dihapus, dan jadwal berulang
  pindah ke jam berikutnya yang belum lewat dari batas itu. Jadi kalau PC mati sepanjang
  akhir pekan, jadwal "tiap hari jam 7" tetap jalan Senin pagi saat PC dinyalakan 07.10.
- Pengingat sekali, bulanan, atau tahunan yang terlewat diucapkan begitu Cyrene jalan lagi,
  semuanya dalam satu kalimat ("Master, ada pengingat yang terlewat jam 07.00: ..."), dan
  dikirim ke HP sebagai `Terlewat (07.00): ...` (dengan tanggalnya kalau bukan hari ini,
  misalnya `Terlewat (10-10-2026 07.00): ...`). Jadi ulang tahun yang jamnya lewat saat PC
  mati tetap disebut, walau baru diucapkan siang itu atau beberapa hari kemudian. Pengingat
  harian, hari kerja, akhir pekan, atau mingguan yang terlewat hanya dikirim ke HP, karena
  jamnya akan datang lagi dalam seminggu: **tanpa notifikasi HP** (`notify.enabled: false`,
  bawaan) yang seperti itu tidak diucapkan maupun dikirim, dan jejaknya hanya baris WARNING di
  log.
- Jadwal dicek setiap 15 detik, jadi bisa jalan sampai sekitar 15 detik setelah menitnya.
  Jam pada menit yang sedang berjalan masih diterima ("satu menit lagi" yang sampai sedikit
  terlambat) dan langsung jalan pada pengecekan berikutnya. Jadwal yang jatuh tempo selagi
  rutinitas lain masih berjalan (misalnya perangkatnya lama tidak menjawab) tetap dijalankan
  sesudahnya: batas telat dihitung dari awal pengecekan itu, karena Cyrene sibuk, bukan mati.
- **PC tidak pernah dijadwalkan.** Mematikan, me-restart, atau menidurkan PC tetap hanya
  lewat perintah suara langsung dengan konfirmasi. Jadwal atau rutinitas yang menyebut PC
  (`type: pc`) ditolak, juga saat akan dijalankan.
- **Colokan yang menyalakan PC dijaga seperti PC**, asal ditandai `powers_pc: true` di
  `config.yaml` (contoh `colokan_pc` sudah begitu): jadwal dan rutinitas yang memakainya
  ditolak, juga saat akan dijalankan, karena mematikannya selagi Windows menyala sama
  dengan mencabut kabel PC. Colokan yang belum ditandai dianggap colokan biasa.
- `waifu chat` menjalankan jadwal hanya kalau tidak ada `waifu run` yang sedang berjalan:
  pengingat dicetak sebagai `Cyrene: ...`, tanpa suara dan tanpa notifikasi HP. Kalau
  `waifu run` sedang berjalan, yang menjalankan jadwal adalah Cyrene di `waifu run`, dan
  chat hanya membuat, menampilkan, dan membatalkan jadwal. Jangan menyalakan `waifu run`
  selagi chat yang dibuka lebih dulu masih terbuka: keduanya akan mengecek file yang sama,
  dan jadwal yang diambil chat hanya dicetak di terminal.
- **Dengan `--dry-run`** (`waifu run` maupun `waifu chat`) jadwal memakai file sementara
  yang kosong dan dihapus saat ditutup. Jadwal yang dibuat di sana hanya percobaan pada
  perangkat simulasi, dan `data\schedule.json` tidak disentuh: jadwal asli tidak dijalankan
  pada perangkat simulasi.

### Menulis rutinitas di config.yaml

Hanya Master yang menulis rutinitas. Cyrene bisa menjalankannya (lewat suara, jadwal, atau
`waifu routine run`) tapi tidak bisa mengubahnya.

```yaml
routines:
  tidur:
    description: Mode tidur
    steps:
      - {device: lampu_kamar, command: off}
      - {device: ac, command: temp, value: 26}
  film:
    description: Nonton film
    steps:
      - {device: lampu_kamar, command: brightness, value: 20}
      - {device: proyektor, command: power}
```

- Nama rutinitas: huruf kecil, angka, atau garis bawah. Setiap rutinitas punya 1–20 langkah.
- `device` adalah id dari `devices`; `command` dan `value` sama seperti
  `waifu device <id> <perintah> [angka]` (untuk IR, `temp` dengan `value: 26` mengirim kode
  `temp_26` yang sudah dipelajari).
- Langkah dijalankan berurutan. Langkah yang gagal tidak menghentikan langkah berikutnya, dan
  dia bilang langkah mana yang gagal.
- `after_minutes` (boleh tidak ditulis) menunda satu langkah sekian menit setelah rutinitas
  dijalankan, misalnya `{device: ac, command: off, after_minutes: 180}`. Lihat
  [Mode tidur](#mode-tidur).
- Perangkat yang tidak ada di `devices`, atau PC (`type: pc`), membuat `config.yaml` ditolak
  saat start.
- Colokan yang ditandai `powers_pc: true` (misalnya `colokan_pc`) juga membuat
  `config.yaml` ditolak kalau dijadikan langkah: mematikannya selagi Windows menyala sama
  dengan mencabut kabel.
- Setelah mengubah `config.yaml`, jalankan ulang `waifu run`. Cek dengan `waifu check`
  (`✓ Rutinitas tidur: ...`, atau `✗` dengan langkah yang tidak bisa jalan, misalnya kode IR
  yang belum dipelajari), lalu coba dengan `waifu routine run tidur --dry-run`.

### Mode tidur

Satu langkah rutinitas bisa **ditunda** dengan `after_minutes`: langkah itu tidak dijalankan
sekarang, tapi masuk jadwal sebagai perintah sekali, sekian menit setelah rutinitasnya
dijalankan. Cocok untuk tidur dengan AC menyala, lalu AC mati sendiri tiga jam kemudian:

```yaml
routines:
  tidur:
    description: Mode tidur
    steps:
      - {device: lampu_kamar, command: off}
      - {device: ac, command: temp, value: 26}
      - {device: ac, command: off, after_minutes: 180}   # 3 jam kemudian
```

```text
(22.00)  Master : "Cyrene, mode tidur."
         Cyrene : "Lampu sudah mati, AC di dua puluh enam derajat, dan AC akan mati jam satu
                   nanti. おやすみなさい, Master."
(01.00)  AC mati tanpa suara apa pun.
```

- `after_minutes` adalah bilangan bulat menit dari 1 sampai 1440 (24 jam): `90` untuk satu
  setengah jam, `180` untuk tiga jam. Tulisan seperti `3h` atau `"180"` (pakai tanda kutip)
  membuat `config.yaml` ditolak saat start, dengan pesan cara menulisnya.
- Jedanya dihitung dari saat rutinitas dijalankan, bukan dari langkah sebelumnya: dua langkah
  dengan `after_minutes: 180` jalan pada menit yang sama. Rutinitas yang dijadwalkan
  ("jalankan mode tidur jam 11 malam") menghitungnya dari jam rutinitas itu jalan.
- Langkah yang ditunda jalan diam-diam, seperti [perintah perangkat terjadwal](#cara-jadwal-dijalankan):
  dia hanya bicara (dan mengirim notifikasi kalau `notify.enabled: true`) kalau gagal, misalnya
  "Master, jadwal ac off dari rutinitas tidur jam 01.00 gagal: ...".
- Langkahnya tersimpan di `data\schedule.json`, jadi tetap ada walau `waifu run` dijalankan
  ulang, dan terlihat di `waifu schedule list` (dan saat Master bertanya "ada jadwal apa
  saja?"):

  ```text
  ID   BERIKUTNYA               ULANG   ISI
  j12  Minggu 18-10-2026 01:00  sekali  perangkat ac off (rutinitas tidur)
  ```

- **Membatalkan:** "Cyrene, batalkan AC mati nanti.", atau `waifu schedule cancel j12` dari
  terminal. Lampu dan AC 26 yang sudah jalan tidak berubah.
- **Menjalankan lagi** menggantikan langkah yang ditunda dari jalannya yang sebelumnya: "mode
  tidur" jam 22.00 lalu sekali lagi jam 23.00 membuat AC mati jam 02.00 saja, tidak dua kali.
  Jadwal yang Master buat sendiri ("matikan AC jam 2") tidak ikut dibatalkan.
- Setiap langkah yang ditunda memakai satu tempat di `schedule.max_entries` (bawaan 50) sampai
  jalan. Kalau jadwal penuh, hanya langkah itu yang gagal (dia bilang jadwalnya penuh); langkah
  lain sudah jalan.
- `waifu routine run tidur` menjalankan langkah yang sekarang dan menjadwalkan yang ditunda:

  ```text
  Menjalankan rutinitas tidur...
  ✓ Langkah 1 (lampu_kamar off): Lampu kamar dimatikan.
  ✓ Langkah 2 (ac temp 26): AC Samsung: sinyal 'temp_26' sudah dikirim.
  ✓ Langkah 3 (ac off setelah 3 jam): Dijadwalkan besok jam 01.00 (j12).
  Langkah yang ditunda dijalankan pada jamnya oleh Cyrene yang sedang berjalan ('waifu run'): ...
  ```

  Dengan `--dry-run` langkah yang ditunda hanya dicoba di jadwal sementara yang hilang saat
  ditutup; `data\schedule.json` tidak disentuh.

**PC dan `waifu run` harus tetap menyala sampai jamnya.** Mode tidur tidak menidurkan PC dan
tidak mencegah Windows tidur. Kalau Windows diatur tidur sendiri (Settings → System → Power,
di laptop Power & battery; lalu bagian Screen and sleep, di Windows 11 24H2 ke atas Screen,
sleep, & hibernate timeouts), pilih **Never** untuk pilihan sleep (*... sleep after*), atau
waktu yang lebih lama dari jeda terlama; layar boleh mati. Langkah yang terlewat lebih dari
`schedule.late_grace_minutes` menit (bawaan 30) dibuang dan hanya dicatat di log (WARNING),
tanpa suara atau notifikasi.

Yang tidak bisa ditunda (`config.yaml` ditolak saat start):

- PC (`type: pc`) dan colokan `powers_pc: true`, sama seperti langkah biasa.
- `play_pause` dan `mute` pada perangkat musik (`type: media`): tombolnya bolak-balik, jadi bisa
  malah memutar musik atau menyalakan suara saat Master tidur. Tunda `stop` atau `volume`.
- Langkah apa pun kalau `schedule.enabled: false`: langkah yang ditunda jalan lewat jadwal.

Jangan menunda perintah IR yang bolak-balik, seperti `power` proyektor atau TV: Cyrene tidak
bisa membedakannya dari `off`, jadi kalau perangkatnya sudah mati, perintah itu malah
menyalakannya. Untuk AC, pelajari tombol mati sebagai `off` (`waifu learn-ir ac off`). Perangkat
yang dihapus atau diganti id-nya di `config.yaml` sebelum jamnya membuat langkah itu gagal saat
jamnya tiba (diucapkan dan dikirim ke HP seperti jadwal lain yang gagal).

### Mengatur jadwal dari terminal

```powershell
waifu schedule list                 # semua jadwal, yang paling dekat dulu
waifu schedule cancel j3            # batalkan satu jadwal (id dari list)
waifu schedule clear                # hanya menghitung yang akan dihapus
waifu schedule clear --yes          # hapus semua jadwal
waifu routine list                  # setiap rutinitas dan langkahnya
waifu routine run tidur --dry-run   # coba di perangkat simulasi
waifu routine run tidur             # jalankan sekarang di perangkat asli; langkah yang ditunda masuk jadwal
```

`waifu schedule list` menampilkan jam berikutnya setiap jadwal:

```text
ID  BERIKUTNYA               ULANG            ISI
j1  Selasa 13-10-2026 15:00  sekali           pengingat "Master, sudah jam tiga, waktunya minum obat!"
j3  Selasa 13-10-2026 23:00  tiap hari        rutinitas tidur
j2  Rabu 14-10-2026 02:00    sekali           perangkat ac off
j4  Senin 19-10-2026 07:00   tiap Senin       pengingat "Master, buang sampah!"
j5  Selasa 20-10-2026 08:00  tiap tanggal 20  pengingat "Master, bayar listrik!"
j6  Rabu 05-05-2027 08:00    tiap 5 Mei       pengingat "Master, hari ini ulang tahun Ibu!"
```

Jadwal bulanan dan tahunan menyebut tanggal ulangnya sendiri: jadwal "tiap tanggal 31" yang
berikutnya jatuh 30 November tetap tertulis `tiap tanggal 31`.

Jadwal baru dibuat lewat suara, `waifu chat` (tanpa `--dry-run`), atau `waifu routine run`
untuk langkah yang ditunda (lihat [Mode tidur](#mode-tidur)). Perintah `waifu schedule` tetap
jalan walau `schedule.enabled: false`, dan Cyrene yang sedang berjalan melihat perubahannya
dalam 15 detik. `waifu check` menampilkan `✓ Jadwal: <n> entri (data\schedule.json)`.

### File data\schedule.json

- Jadwal hanya ada di `data\schedule.json` di PC ini. Seluruh `data\` ada di `.gitignore`,
  jadi tidak pernah di-commit. Isinya bisa pribadi (misalnya pengingat minum obat): jangan
  dibagikan.
- Batasnya `schedule.max_entries` jadwal (bawaan 50), paling jauh 366 hari ke depan, dan
  teks pengingat paling panjang 200 karakter.
- Jadwal bulanan dan tahunan menyimpan tanggal ulangnya di kunci `day` (misalnya `"day": 31`
  untuk jadwal yang sekarang jatuh 30 November). Kalau mengedit `at` jadwal seperti itu dengan
  tangan, `day` harus cocok dengan tanggalnya, atau file dianggap rusak (lihat di bawah).
- **Turun ke versi sebelum v1.43** selagi ada jadwal mingguan, bulanan, atau tahunan membuat
  versi lama menganggap `data\schedule.json` rusak dan memindahkannya ke samping
  (`.corrupt-...`), jadi semua jadwal hilang dari versi itu. Batalkan dulu jadwal seperti itu
  sebelum turun versi.
- **Edit hanya saat Cyrene mati**, seperti `data\memory.json`: di antara dua program, yang
  terakhir menulis yang menang. `waifu schedule` membaca file tepat sebelum mengubahnya, jadi
  aman dipakai selagi dia berjalan.
- **File yang rusak tidak pernah ditimpa.** Kalau isinya bukan jadwal yang benar, Cyrene
  mengganti namanya menjadi `schedule.json.corrupt-<tanggal>-<jam>`, menulis error di log,
  lalu mulai dengan jadwal kosong. Selama file itu masih ada, `waifu check` menunjukkannya
  dengan `!`. Untuk memulihkan: matikan Cyrene, perbaiki file `.corrupt-...` itu, lalu ganti
  namanya kembali menjadi `schedule.json`.
- Dengan `brain.backend: claude`, teks pengingat ikut terkirim ke API Anthropic sebagai bagian
  dari obrolan (seperti kalimat lainnya); dengan notifikasi HP, teks pengingat juga dikirim ke
  `ntfy.sh`.
- Untuk mematikan jadwal: `schedule.enabled: false`. Dia tidak lagi membuat atau menjalankan
  jadwal, tapi filenya tidak dihapus; hapus isinya dengan `waifu schedule clear --yes` kalau
  mau.

## Alarm bangun

Pengingat diucapkan sekali saja, dan berbisik kalau [mode bisik](#mode-bisik) menyala, jadi
Master yang masih tidur bisa tidak mendengarnya. **Alarm** terus membangunkan: pada jamnya Cyrene
bilang "Master, bangun!", lalu mengulanginya setiap menit, makin keras, sampai Master bilang sudah
bangun. Alarm masuk jadwal yang sama dengan pengingat (lihat
[Jadwal, pengingat, dan rutinitas](#jadwal-pengingat-dan-rutinitas)), jadi sudah menyala dari
awal di `waifu run`, dengan otak Ollama maupun Claude.

```text
Master : "Cyrene, bangunkan aku jam 6, ada kuliah pagi."
Cyrene : "Siap, alarm jam enam pagi. Alarmnya cuma bunyi kalau PC menyala dan aku jalan ya, Master."
(06.00)  Cyrene : "Master, bangun! Sudah jam 06.00. Kuliah pagi."
(06.01)  Cyrene : "Ayo bangun, Master! Sudah jam 06.01. Kuliah pagi."   (lebih keras)
Master : "Aku sudah bangun."
Cyrene : "Oke Master, alarmnya kumatikan."
```

### Contoh kalimat alarm

- **Sekali:** "Cyrene, bangunkan aku jam 6.", "alarm jam 5.30 besok", "pasang alarm 20 menit
  lagi". Jam yang sudah lewat hari ini berarti besok.
- **Dengan keterangan:** "alarm jam 5.30 besok buat berangkat kerja": keterangannya ikut disebut
  di setiap bunyi ("... Berangkat kerja.").
- **Berulang:** "alarm tiap hari kerja jam 6", "bangunkan aku tiap hari jam 5", "alarm tiap
  Sabtu jam 8": semua pengulangan jadwal bisa (harian, hari kerja, akhir pekan, mingguan,
  bulanan, tahunan).
- **Melihat dan membatalkan:** "alarmku apa aja?", "batalkan alarm jam 6". Alarm juga muncul di
  `waifu schedule list` (`alarm "berangkat kerja"`) dan bisa dibatalkan dengan
  `waifu schedule cancel <id>`.

### Saat alarm berbunyi

- Dia bilang "Master, bangun! Sudah jam 06.00." (dengan keterangannya), lalu lagi setiap
  `alarm.repeat_seconds` detik (bawaan 60) dengan kalimat bergantian. Setiap bunyi 20 persen
  lebih keras: mulai dari volume suaranya di siang hari (lihat [Volume suaraku](#volume-suaraku))
  sampai paling keras 150 persen. **Mode bisik tidak berlaku untuk alarm.**
- Alarm tetap berbunyi selama [teman fokus](#teman-fokus) atau [mode game](#mode-game). Bunyinya
  tidak pernah memotong omongan Master atau jawabannya sendiri: bunyi berikutnya menunggu sampai
  kalimatnya selesai.
- Berhenti sendiri setelah `alarm.max_minutes` menit (bawaan 10) kalau tidak dimatikan.
- Sapaan pagi dan hal lain yang dia mulai sendiri menunggu sampai alarmnya berhenti; pengingat
  lain di menit yang sama tetap diucapkan, sesudah bunyinya.
- Dua alarm pada menit yang sama berbunyi sebagai satu, dengan kedua keterangannya.
- Dengan [notifikasi ke HP](#3-notifikasi-ke-hp-ntfy) (`notify.enabled: true`), bunyi pertama
  setiap alarm juga dikirim ke HP ("Master, bangun! Alarm jam 06.00."). Kalau
  [Cyrene di HP](#cyrene-di-hp) sudah diatur, mengetuk notifikasinya membuka halamannya langsung
  dengan tombol **Matikan alarm**. Notifikasinya memakai prioritas biasa ntfy: untuk bunyi yang
  lebih keras, atur suara notifikasi topik itu di aplikasi ntfy. `alarm.push: false` mematikan
  kiriman ini.

### Mematikan dan menunda

- **Ucapkan saja, tanpa memanggil namanya:** "aku sudah bangun", "udah bangun", "aku bangun",
  "dah bangun", "matikan alarm", "stop alarm", atau "hentikan alarm" mematikannya. "Tunda",
  "tunda sebentar", "snooze", atau "sebentar lagi" menundanya `alarm.snooze_minutes` menit
  (bawaan 5); "tunda 10 menit", "tunda 5 menit lagi", atau "lima menit lagi" sekian menit
  (1-30). Dia menjawab langsung tanpa otak: "Oke, kubangunkan lagi jam 06.06."
- **Hanya kalimat itu persis**, boleh ditambah namanya, "Master", "dong", "iya", atau "oke".
  "Matikan lampu", "stop" saja, atau "aku sudah bangun, nyalakan lampu" dijawab seperti biasa,
  dan alarmnya jalan terus. Kalimat lain soal alarm ("Cyrene, matiin alarmnya ya, aku udah
  melek") tetap sampai ke otaknya, yang juga bisa mematikan atau menunda alarm.
- Mikrofon dimatikan selagi dia berbicara, jadi ucapkan sesudah kalimat bunyinya selesai.
- [Verifikasi suara](#mendaftarkan-suara-master) tetap berlaku. Kalau suara bangun tidur tidak
  dikenali, pakai tombolnya.
- **Tombol:** selagi alarm berbunyi, [Tombol cepat di HP](#tombol-cepat-di-hp) dan halaman
  [`waifu tombol`](#tombol-cepat-di-pc) menampilkan grup **Alarm** paling atas: **Matikan
  alarm** dan **Tunda 5 menit**. Di HP panelnya terbuka sendiri begitu halamannya dibuka lagi.
- Alarm yang ditunda menjadi alarm sekali di jadwal (dibulatkan ke atas ke menit penuh), jadi
  terlihat di `waifu schedule list` dan bisa dibatalkan. Kalau jadwal sudah penuh, penundaannya
  ditolak dan alarmnya jalan terus.
- Double-click di avatar hanya memotong satu kalimat bunyi, bukan alarmnya.

### Hanya selama PC menyala

- Alarm hanya berbunyi selama `waifu run` jalan dan PC menyala, tidak sleep atau hibernate.
  Cyrene tidak bisa membangunkan PC: biarkan PC menyala malam itu, dan atur Windows supaya tidak
  sleep sendiri.
- Suaranya keluar dari speaker PC dengan volume Windows: kalau volume Windows nol atau di-mute,
  alarmnya tidak terdengar. 150 persen hanya mengeraskan suaranya sendiri. Notifikasi HP adalah
  cadangannya.
- Alarm yang terlewat (PC mati atau sleep, atau Cyrene telat lebih dari 2 menit) tidak
  dibunyikan terlambat. Dengan notifikasi HP, HP menerima "Alarm jam 06.00 terlewat." (dengan
  tanggalnya kalau bukan hari ini), dan alarm berulang tetap berbunyi di jadwal berikutnya.
- Jadwal dicek setiap 15 detik, jadi bunyi pertama bisa telat sampai sekitar 15 detik.
- Kalau `waifu run` dijalankan ulang selagi alarm berbunyi, alarmnya lanjut (tanpa notifikasi
  kedua) sampai batas menitnya.
- Tanpa `waifu run`, `waifu chat` hanya mencetak alarm yang jatuh tempo sekali. Selagi `waifu run`
  berjalan, "matikan alarm" yang diketik di `waifu chat` mematikan alarm yang sedang berbunyi.

### Pengaturan alarm

Sudah menyala tanpa ditulis. Untuk mengubahnya, tulis di `config.yaml`, lalu jalankan ulang
`waifu run`:

```yaml
alarm:
  repeat_seconds: 60     # jarak antar bunyi, dalam detik (30-300)
  max_minutes: 10        # berhenti sendiri setelah N menit (1-30)
  snooze_minutes: 5      # "tunda" tanpa angka (1-30)
  push: true             # bunyi pertama juga ke HP (hanya kalau notify menyala)
```

`alarm.enabled: false` mematikan tool alarm; alarm yang sudah ada di jadwal hanya diucapkan sekali
seperti pengingat. Alarm butuh jadwal (`schedule.enabled: true`). `waifu check` punya baris
`✓ Alarm: 1 alarm, bunyi tiap 60 detik sampai 10 menit; hanya saat 'waifu run' jalan dan PC
menyala` (dengan `, juga ke HP` kalau notifikasi HP menyala, dan `; sedang berbunyi sampai 06:10`
selagi berbunyi).

### Alarm dan mode tamu

[Mode tamu](#mode-tamu) tidak menghentikan alarm: alarm tetap berbunyi, dan memasang, mematikan,
atau menunda alarm tetap bisa. Daftar alarm dan membatalkannya ditutup, karena isinya keterangan
dan jadwal Master. Mode tamu selalu berakhir paling lambat jam 05:00, jadi biasanya sudah selesai
sebelum alarm pagi.

### Privasi alarm

Alarm disimpan di `data\schedule.json` seperti jadwal lain, hanya di PC ini. Selagi berbunyi,
`data\alarm.json` berisi id, jam, dan keterangan alarmnya, lalu dihapus saat alarm berhenti. Dengan
Claude, keterangannya ke API Anthropic seperti teks pengingat; dengan notifikasi HP, bunyi pertama
dan alarm yang terlewat ke `ntfy.sh`.

## Daftar tugas

Selain jadwal pada jam tertentu, Cyrene menyimpan **daftar tugas**: hal yang perlu Master
kerjakan, dengan tenggat (tanggal) kalau ada, tanpa jam. Daftar tugas sudah menyala dari awal
(`todo.enabled: true`) di `waifu run` dan `waifu chat` (juga dengan `--dry-run`, yang
memalsukan perangkat tapi memakai daftar tugas yang asli).

```text
Master : "Cyrene, tambah tugas: beli susu."
Cyrene : "Sudah kucatat, Master."
Master : "Catat tugas kerjakan laporan, tenggatnya Jumat."
Cyrene : "Oke, laporan tenggatnya Jumat, 23 Oktober."
Master : "Tugasku apa aja?"
Cyrene : "Ada tiga, Master. Bayar listrik sudah lewat tenggatnya kemarin, terus laporan Jumat nanti."
Master : "Beli susu sudah."
Cyrene : "Yay, satu selesai!"
```

### Contoh kalimat

- **Menambah:** "Cyrene, tambah tugas: beli susu.", "Cyrene, catat tugas kerjakan laporan,
  tenggatnya Jumat." Dia mengubah "besok", "Jumat", atau "minggu depan" menjadi tanggal dari
  tanggal hari ini, yang dia tahu dari setiap kalimatmu, dan menyebut tanggalnya.
- **Melihat:** "Cyrene, tugasku apa aja?", "Ada yang telat nggak?" Dia menyebut jumlahnya dan
  yang paling mendesak (yang terlambat dulu, lalu yang tenggatnya paling dekat), bukan
  membacakan semuanya, kecuali Master minta daftar lengkapnya.
- **Selesai, dihapus, atau diubah:** "Beli susu sudah.", "Tugas laporan selesai.", "Hapus tugas
  beli susu.", "Ganti tenggat laporan jadi Senin.", "Laporan ternyata belum selesai." (dibuka
  lagi).
- **Yang sudah selesai:** "Cyrene, minggu ini aku sudah menyelesaikan apa saja?" Tugas yang
  selesai masih diingat `todo.keep_done_days` hari (bawaan 7), lalu dilupakan.
- **Sapaan pagi** dan "Cyrene, hari ini ada apa?" ikut menyebut tugas yang tenggatnya hari ini
  atau sudah lewat (lihat [Sapaan pagi dan ringkasan harian](#sapaan-pagi-dan-ringkasan-harian)).

### Tugas atau pengingat?

- **Tugas** tidak punya jam dan tidak pernah diucapkan sendiri pada waktunya: dia menyebutnya
  hanya kalau ditanya dan di sapaan pagi. Cocok untuk "kerjakan laporan sebelum Jumat".
- **Pengingat** adalah jadwal: diucapkan pada jamnya selama `waifu run` menyala dan dikirim ke
  HP (lihat [Jadwal, pengingat, dan rutinitas](#jadwal-pengingat-dan-rutinitas)). Tugas yang
  menyebut jam ("tambah tugas telepon dokter jam 4 sore") lebih cocok jadi pengingat: dia bilang
  begitu, atau membuat tugas dan pengingatnya sekaligus kalau Master minta.
- Tidak ada tugas berulang, sub-tugas, prioritas (urutannya selalu: terlambat, menurut
  tenggat, lalu tanpa tenggat), atau sinkron ke Google Calendar dan layanan lain.

### Mengatur tugas dari terminal

```powershell
waifu todo                                          # tugas yang belum selesai (sama dengan todo list)
waifu todo add kerjakan laporan --due 2026-10-23    # tambah satu tugas; tenggat opsional, YYYY-MM-DD
waifu todo done t3                                  # tandai selesai (id dari list)
waifu todo remove t3                                # hapus satu tugas, yang belum atau sudah selesai
waifu todo list --done                              # juga tugas yang selesai beberapa hari terakhir
```

`waifu todo` menampilkan yang terlambat dulu, lalu menurut tenggat, lalu yang tanpa tenggat:

```text
ID  TENGGAT                       TUGAS
t3  Kamis 15-10-2026 (terlambat)  bayar listrik
t4  Jumat 16-10-2026 (hari ini)   kirim tugas kuliah
t2  Jumat 23-10-2026              kerjakan laporan
t1  -                             beli susu
```

Aturan yang sama berlaku seperti lewat suara (rahasia, panjang, jumlah); kalau ditolak,
perintahnya menampilkan `✗` dengan alasannya. Perintah ini tetap jalan walau
`todo.enabled: false`, dan Cyrene yang sedang berjalan langsung melihat perubahannya.
`waifu check` menampilkan `✓ Daftar tugas: <n> terbuka`, atau `! Daftar tugas mati`.

### File data\todo.json dan privasi tugas

- Tugas hanya ada di `data\todo.json` di PC ini. Seluruh `data\` ada di `.gitignore`, jadi
  tidak pernah di-commit.
- **Rahasia tidak pernah disimpan.** Seperti catatan ingatan, tugas yang berisi kata sandi,
  PIN, OTP, token, atau nomor kartu beserta isinya ditolak oleh kode Cyrene sendiri: "ganti
  PIN ATM-ku 4821", "password wifi: hijau123", "kode OTP 482910", dan "token PLN 1234 5678
  9012 3456 7890" tidak dicatat. Menyebut rahasianya saja boleh: "ganti PIN ATM", "ingatkan
  ganti password wifi", "minta kode OTP ke bank", dan "beli token listrik" tetap dicatat,
  juga "beli token listrik 100000": setelah "token listrik" atau "token PLN" hanya 20 digit
  tokennya yang dianggap isi. Setelah kata rahasia lain, tulis nominal sebagai "Rp100.000",
  "Rp100rb", atau "100 ribu"; angka polos 4 digit atau lebih dalam empat kata setelah kata
  rahasia ("beli token game 100000"), atau 5 digit atau lebih di sisa kalimatnya, dianggap
  isinya dan ditolak. Angka di kalimat berikutnya (setelah koma, "lalu", atau "terus") tidak:
  "ganti PIN ATM terus transfer 500000 ke ibu" tetap dicatat.
- Batasnya `todo.max_tasks` tugas yang belum selesai (bawaan 100), masing-masing paling panjang
  `todo.max_chars` karakter (bawaan 200); kalau penuh, selesaikan atau hapus tugas lama dulu.
- Dengan `brain.backend: claude`, tugas yang dia baca (saat Master bertanya dan di sapaan pagi)
  ikut terkirim ke API Anthropic sebagai bagian dari obrolan; dengan Ollama daftarnya tidak
  keluar dari PC (jawaban yang menyebutnya tetap dijadikan suara oleh edge-tts kalau engine
  `edge` dipakai). Sapaan pagi yang menyebut tugas juga dikirim ke `ntfy.sh` kalau notifikasi
  HP menyala, dan tugas yang Master ucapkan tercatat di log seperti setiap kalimat yang dia
  dengar (`Heard ...`).
- **Edit hanya saat Cyrene mati**, seperti `data\memory.json`: di antara dua program, yang
  terakhir menulis yang menang. `waifu todo` membaca file tepat sebelum mengubahnya, jadi aman
  dipakai selagi dia berjalan.
- **File yang rusak tidak pernah ditimpa.** Kalau isinya bukan daftar tugas yang benar, Cyrene
  mengganti namanya menjadi `todo.json.corrupt-<tanggal>-<jam>`, menulis error di log, lalu
  mulai dengan daftar kosong. Selama file itu masih ada, `waifu check` menunjukkannya dengan
  `!`. Untuk memulihkan: matikan Cyrene, perbaiki file `.corrupt-...` itu, lalu ganti namanya
  kembali menjadi `todo.json`.
- Untuk mematikan daftar tugas: `todo.enabled: false` (contohnya ada di `config.example.yaml`).
  Dia tidak lagi membaca atau mengubah tugas, juga tidak di sapaan pagi, tapi filenya tidak
  dihapus.

## Musik, volume, dan aplikasi PC

Cyrene bisa menekan tombol media dan volume Windows, dan membuka aplikasi atau game yang
Master daftarkan di `config.yaml`. Keduanya perangkat biasa (`type: media` dan
`type: apps`), jadi juga bisa dipakai di jadwal dan rutinitas, dicoba dengan
`waifu device`, dan disimulasikan dengan `--dry-run`.

```text
Master : "Cyrene, pause musiknya."
Cyrene : "Tombol jedanya sudah kutekan, Master."
Master : "Cyrene, volume 30."
Master : "Cyrene, buka Genshin."
Cyrene : "Genshin sudah dibuka. Selamat main, Master!"
```

### Contoh kalimat

- **Musik:** "Cyrene, pause musiknya.", "lanjutkan lagunya", "lagu berikutnya",
  "lagu sebelumnya", "stop musiknya".
- **Volume:** "Cyrene, kecilkan volume" (turun sekitar 10%), "kecilkan volume 20 persen",
  "besarkan volume sedikit", "volume 30" (diatur ke sekitar 30%), "mute" (sekali lagi untuk
  menyalakan suara).
- **Aplikasi:** "Cyrene, buka Spotify.", "Cyrene, buka Genshin." Hanya aplikasi di daftar
  `apps`.
- **Terjadwal:** "Cyrene, jam 7 pagi buka Spotify.", "Cyrene, matikan musik jam 11 malam."

Tombol media berlaku untuk pemutar apa pun yang sedang terbuka: Spotify, YouTube di
browser, pemutar video, atau game, sama seperti tombol media di keyboard. Windows yang
memilih pemutarnya (biasanya yang terakhir memutar suara).

**Saat jendela yang aktif adalah game yang berjalan sebagai administrator** (game dan
launcher HoYoverse, dan game lain dengan anti-cheat), tombol dari Cyrene tidak sampai:
Windows menahannya diam-diam, tanpa pesan error. Cyrene hanya tahu tombolnya sudah ditekan,
bukan apakah musik atau volumenya benar-benar berubah, jadi jawabannya pun begitu. Pindah
dulu ke jendela lain (Alt+Tab), atau pakai tombol media di keyboard selama main game itu.

### Menambahkan di config.yaml

Contoh yang sama ada di akhir `config.example.yaml` (hapus tanda `#` di depannya):

```yaml
devices:
  # ... perangkat lain ...
  - id: musik
    name: Musik PC
    aliases: [musik, lagu, media, volume, suara]
    type: media

  - id: aplikasi
    name: Aplikasi PC
    aliases: [aplikasi, game]
    type: apps
    apps:
      spotify: "C:/Users/<nama>/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Spotify.lnk"
      genshin: "C:/Users/<nama>/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Genshin Impact.lnk"
      cyberpunk: "steam://rungameid/1091500"
```

Alias `volume` dan `suara` membuat "kecilkan volume" berarti volume PC ("kecilkan suaramu"
tetap suaranya sendiri, lihat [Volume suaraku](#volume-suaraku)). Kalau proyektor atau
TV juga punya tombol volume yang sudah dipelajari, Cyrene bisa bertanya perangkat mana yang
dimaksud; sebut saja perangkatnya ("kecilkan volume proyektor").

Perintah perangkat `media`:

| Perintah | Yang terjadi |
|---|---|
| `play_pause` | jeda, atau putar lagi kalau sedang berhenti (satu tombol bolak-balik) |
| `next` / `previous` | lagu berikutnya / sebelumnya |
| `stop` | hentikan pemutaran |
| `mute` | bisukan, atau nyalakan suara lagi (satu tombol bolak-balik) |
| `volume_up` / `volume_down` + angka 1–100 | naik / turun sekitar sekian persen (dibulatkan ke bawah ke angka genap, paling sedikit 2%); tanpa angka sekitar 10% |
| `volume` + angka 0–100 | atur ke sekitar angka itu (dibulatkan ke bawah ke angka genap) |

Semua angka volume adalah persen, seperti yang diucapkan: "kecilkan volume 20 persen" menjadi
`volume_down` 20. Windows tidak punya tombol untuk level volume tertentu, jadi `volume 30`
menekan tombol volume turun 50 kali (sampai 0%) lalu naik 15 kali; tiap tekanan sekitar 2%.
Indikator volume Windows ikut muncul di layar, seperti saat tombol volume keyboard ditekan.

Tombol volume mengubah volume Windows yang juga dipakai suara Cyrene (kecuali
`audio.output_device` menunjuk speaker lain). Setelah `volume 0` atau `mute`, jawaban dan
pengingat lisannya tidak terdengar sampai volumenya dinaikkan lagi; hanya notifikasi ntfy di
HP yang tetap sampai, kalau diaktifkan. Untuk mengecilkan atau mengeraskan suara Cyrene saja,
tanpa musik dan game, bilang "kecilkan suaramu" (lihat [Volume suaraku](#volume-suaraku)).

Perangkat `apps` punya perintah `open_<nama>` untuk setiap aplikasi, urut seperti di
`config.yaml` (`open_spotify`, `open_genshin`, `open_cyberpunk`). Aturannya:

- Nama aplikasi: huruf kecil, angka, atau garis bawah; 1–30 aplikasi per perangkat.
- Isinya path lengkap dengan huruf drive (`C:/...`) ke program `.exe`, shortcut `.lnk`, atau
  shortcut internet `.url`; atau URL launcher `steam://...` atau
  `com.epicgames.launcher://...`.
- Skrip (`.bat`, `.cmd`, `.ps1`, `.vbs`), path tanpa huruf drive, dan alamat lain seperti
  `https://...` membuat `config.yaml` ditolak saat start.
- Shortcut (`.lnk`, `.url`) membuka apa pun yang ditunjuknya, termasuk skrip atau situs web.
  Daftarkan hanya shortcut yang kamu buat sendiri atau percayai (dari menu Start, Steam, atau
  Epic). `waifu check` membaca alamat di shortcut `.url` dan menandainya ✗ kalau bukan
  `steam://` atau `com.epicgames.launcher://`; tujuan shortcut `.lnk` tidak dicek.
- Program `.exe` dijalankan di foldernya sendiri, seperti saat dibuka dari Explorer.
  Shortcut memakai folder "Start in" miliknya sendiri.
- Tulis path dengan `/` seperti contoh. Path dengan `\` harus di antara tanda kutip tunggal
  (`'C:\Users\...'`), karena di dalam tanda kutip ganda YAML membaca `\` sebagai kode khusus.
- Ada atau tidaknya file dicek oleh `waifu check` dan saat aplikasinya dibuka, bukan saat
  start: game yang dipindah tidak membuat Cyrene gagal jalan.

Setelah mengubah `config.yaml`, jalankan ulang `waifu run`, lalu cek:

```powershell
waifu check                                   # ✓ / ✗ untuk setiap aplikasi
waifu device musik volume 30 --dry-run        # simulasi, tidak ada tombol yang ditekan
waifu device aplikasi open_spotify --dry-run  # simulasi, tidak ada yang dibuka
waifu device aplikasi open_spotify            # benar-benar membuka Spotify
```

### Mencari shortcut aplikasi dan game

Shortcut dari menu Start paling mudah, dan biasanya tetap benar setelah aplikasinya update:

1. Buka menu Start dan cari aplikasinya (misalnya "Spotify").
2. Klik kanan > **Open file location**. Explorer membuka folder shortcut-nya, biasanya
   `C:\Users\<nama>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\` (shortcut untuk
   user kamu saja) atau `C:\ProgramData\Microsoft\Windows\Start Menu\Programs\` (untuk semua
   user). Nama folder dan file-nya bisa berbeda dari contoh di atas.
3. Klik kanan shortcut-nya > **Copy as path**, lalu tempel di `apps`. Hasil salinannya
   memakai `\` dan tanda kutip ganda: ganti `\` dengan `/`, atau ganti tanda kutipnya dengan
   kutip tunggal.

- Program `.exe`-nya juga boleh dipakai: klik kanan shortcut tadi > **Open file location**
  sekali lagi membuka folder programnya.
- Shortcut launcher (HoYoPlay, Steam, Epic) membuka launcher-nya. Untuk langsung ke game,
  pakai shortcut game itu sendiri kalau ada, atau URL launcher di bawah.
- Aplikasi dari Microsoft Store tidak punya **Open file location**. Seret aplikasinya dari
  daftar **All apps** di menu Start ke desktop untuk membuat shortcut, pindahkan shortcut itu
  ke folder tetap (misalnya `C:\Users\<nama>\Pintasan\`), lalu pakai path-nya.

**Game Steam** dibuka dengan `steam://rungameid/<App ID>`:

- App ID adalah angka di alamat halaman toko game itu:
  `https://store.steampowered.com/app/1091500/Cyberpunk_2077/` berarti
  `steam://rungameid/1091500`.
- Atau di Steam: Library, klik kanan game > **Properties** > **Updates**; App ID tertulis di
  sana.
- Atau klik kanan game > **Manage** > **Add desktop shortcut**: Steam membuat file `.url` di
  desktop. Path file itu bisa langsung dipakai, atau lihat alamatnya lewat klik kanan >
  **Properties** (kolom URL).

**Game Epic** dibuka lewat shortcut desktop dari Epic Games Launcher, yang juga file `.url`:
pakai path-nya, atau salin alamat `com.epicgames.launcher://...` dari Properties-nya.

### Di rutinitas dan jadwal

```yaml
routines:
  tidur:
    description: Mode tidur
    steps:
      - {device: musik, command: stop}
      - {device: musik, command: volume, value: 20}
      - {device: lampu_kamar, command: off}
      - {device: ac, command: temp, value: 26}
```

- Di rutinitas pakai **`stop`, bukan `play_pause`**. `play_pause` adalah satu tombol
  bolak-balik: kalau musiknya sudah berhenti, rutinitas malah memutarnya lagi. `stop` aman
  dijalankan berkali-kali. Begitu juga `mute`: untuk mengecilkan suara, pakai `volume` dengan
  angka kecil seperti `value: 20` di contoh. `volume 0` juga membuat suara Cyrene dan
  pengingat lisannya besok pagi tidak terdengar (lihat di atas), kecuali ada jadwal atau
  rutinitas pagi yang menaikkan volumenya lagi (misalnya `volume` 30 jam 6 pagi).
- Jadwal lewat suara bekerja seperti perangkat lain: "Cyrene, jam 7 pagi buka Spotify",
  "Cyrene, matikan musik jam 11 malam". Seperti semua jadwal, hanya jalan selama `waifu run`
  menyala.
- Jadwal tidak pernah memakai `play_pause` atau `mute`: jadwal perangkat berjalan diam-diam,
  jadi tombol bolak-balik bisa memutar lagi musik yang sudah dijeda saat Master tidur. Cyrene
  menolaknya dan menjadwalkan `stop` atau `volume` sebagai gantinya. Angka di luar batasnya
  (misalnya `volume` 150) juga langsung ditolak saat dijadwalkan, bukan gagal nanti di
  tengah malam.
- Aplikasi yang dibuka lewat jadwal muncul di layar walau Master sedang tidak di depan PC.

### Yang tidak pernah dilakukan

- **Tidak ada yang pernah ditutup.** Cyrene tidak punya perintah untuk menutup aplikasi,
  mematikan proses, atau keluar dari game.
- **Hanya aplikasi di daftar `apps` yang bisa dibuka.** Perintah suara hanya memilih salah
  satu nama di daftar itu dan membuka isinya: program `.exe` atau URL launcher persis seperti
  tertulis, atau shortcut `.lnk`/`.url` beserta apa pun yang ditunjuknya. Jadi daftarkan
  hanya shortcut yang kamu buat sendiri atau percayai.
- Dia tidak mengetik atau mengklik di dalam aplikasi, tidak memilih lagu atau playlist di
  dalam aplikasi (lagu di YouTube dibuka di browser; lihat
  [Lagu di YouTube](#lagu-di-youtube)), tidak mengatur volume per aplikasi, tidak membaca
  volume yang sedang dipakai, dan tidak memainkan game (macro melanggar ketentuan HoYoverse).

### Pemecahan masalah musik dan aplikasi

- **Game minta izin administrator.** Beberapa game dan launcher (biasanya karena
  anti-cheat-nya) memunculkan jendela izin Windows "Do you want to allow this app to make
  changes to your device?". Klik **Yes**; Cyrene tidak bisa (dan tidak boleh) menjawabnya.
  Kalau dijawab **No**, gamenya tidak terbuka dan dia bilang izin administratornya ditolak.
  Selama jendela izin itu terbuka, Cyrene menunggu jawabannya: lewat suara, jawabannya baru
  keluar setelah jendela itu dijawab; lewat jadwal, jadwal berikutnya (juga pengingat dan
  notifikasinya ke HP) tertunda sampai jendela itu dijawab. Jadi jangan menjadwalkan game
  seperti ini saat tidak ada yang di depan PC.
- **`✗ Aplikasi <nama>: shortcut ini membuka ...` di `waifu check`.** Shortcut `.url` itu
  membuka alamat lain (situs web atau file), bukan game Steam atau Epic. Hapus dari `apps`,
  atau ganti dengan shortcut game yang benar.
- **`✗ Aplikasi <nama>: file tidak ditemukan (...)` di `waifu check`.** Aplikasinya
  dipindah, di-uninstall, atau path-nya salah ketik. Cari lagi shortcut-nya (lihat di atas),
  perbarui `config.yaml`, lalu jalankan ulang `waifu run`. Kalau diminta lewat suara, dia
  bilang file-nya tidak ditemukan dan tidak membuka apa pun.
- **URL launcher** ditandai `✓ ... (URL, tidak dicek)`: satu-satunya cara mengeceknya adalah
  membukanya, dan `waifu check` tidak pernah membuka apa pun. Kalau Steam belum terpasang
  atau App ID-nya salah, dia bilang Windows gagal membukanya, atau Steam menampilkan
  pesannya sendiri.
- **Tombol media tidak berpengaruh.** Pastikan pemutarnya mendukung tombol media (Spotify,
  browser, dan kebanyakan pemutar mendukung). Kalau dua pemutar terbuka, Windows memilih
  salah satunya; jeda yang tidak dipakai. Saat jendela yang aktif adalah program yang
  berjalan sebagai administrator (misalnya game dengan anti-cheat), Windows menahan tombol
  dari Cyrene tanpa pesan error, dan dia tetap bilang tombolnya sudah ditekan: klik desktop
  atau jendela lain, lalu coba lagi.
- **"Windows menolak tombol medianya".** Windows sedang memblokir input, misalnya layar
  terkunci atau jendela izin administrator sedang terbuka.

## Volume suaraku

Suara Cyrene di PC punya volume sendiri, terpisah dari volume Windows: "kecilkan suaramu"
membuatnya lebih pelan tanpa mengecilkan musik atau game. Selalu tersedia, dengan otak Ollama
maupun Claude, tanpa paket tambahan.

```text
Master : "Cyrene, kecilkan suaramu."
Cyrene : "Segini ya, Master, delapan puluh persen."
Master : "Cyrene, nyalakan mode bisik."
Cyrene : "Mode bisik aktif, Master: dari jam sepuluh malam sampai jam enam pagi suaraku paling keras lima puluh persen."
```

### Contoh kalimat

- **Lebih pelan atau lebih keras:** "Cyrene, kecilkan suaramu", "suara kamu kekerasan",
  "jangan keras-keras ngomongnya" (turun 20 persen); "keraskan suaramu", "suara kamu kurang
  keras" (naik 20 persen). Langkahnya selalu berhenti di kelipatan 20 dengan 10 dan 150 di
  ujungnya (dari 75 persen, "kecilkan" menjadi 60), jadi 100 persen selalu bisa dicapai lagi.
- **Angka:** "Cyrene, suaramu 60 persen." Dari 10 sampai 150 persen; 100 persen suara biasanya.
- **Bertanya:** "Suaramu berapa persen?", "mode bisik nyala?"
- **Mode bisik:** "Cyrene, nyalakan mode bisik" dan "matikan mode bisik" (lihat
  [Mode bisik](#mode-bisik)).
- Bisa lewat suara, `waifu chat`, atau chat [Cyrene di HP](#cyrene-di-hp); yang berubah suaranya
  di PC. [Mode tamu](#mode-tamu) tidak menutupnya.

Di PC jawabannya sudah diucapkan dengan volume yang baru, jadi bedanya langsung terdengar
(kecuali saat [mode bisik](#mode-bisik) menahannya; dia bilang begitu). Dia tidak mengubah
volumenya sendiri tanpa diminta.

### Beda dengan volume Windows

| Kalimat | Yang berubah |
|---|---|
| "kecilkan suaramu", "suara kamu", "kecilkan volume suaramu", "mode bisik" | Volume suara Cyrene saja: kata "suaramu" yang menentukan, walau ada kata "volume" |
| "kecilkan volume", "volume 30", "kecilkan musiknya", "volume PC" | Volume Windows lewat perangkat `musik` (lihat [Musik, volume, dan aplikasi PC](#musik-volume-dan-aplikasi-pc)): semua suara PC, termasuk suara Cyrene |

Kalau dia salah tangkap, sebut lebih jelas: "suaramu sendiri" untuk suaranya, "volume PC" untuk
Windows. Volume Windows tetap mengatur semuanya: dengan volume Windows 20%, suaranya 150% pun
tetap pelan, dan setelah `mute` dia tidak terdengar sama sekali.

### Lebih keras dari 100 persen

Di atas 100 persen suaranya diperkuat dengan pembatas lembut supaya tidak pecah: bagian yang
paling keras ditekan sedikit, jadi 150 persen terdengar lebih keras tapi bagian kerasnya tidak
naik sebanyak itu. Di bawah 100 persen suaranya hanya dikecilkan. Gerak mulut avatar tetap
sama besar di volume berapa pun.

### Mode bisik

Mati sampai Master menyalakannya. Selama menyala, setiap malam dari `voice.night.from` (bawaan
22:00) sampai `voice.night.until` (bawaan 06:00) suaranya paling keras `voice.night.level`
(bawaan 50) persen, menurut jam PC. Di luar jam itu volumenya kembali seperti biasa, jadi sapaan
pagi sesudah jam 06:00 terdengar normal. Volume yang sudah lebih pelan dari 50 persen tetap
segitu. "Keraskan suaramu" selama mode bisik menaikkan volume biasanya, dan dia bilang mode bisik
masih menahannya sampai jam 06:00. "Kecilkan suaramu" selama mode bisik langsung lebih pelan dari
yang terdengar (dari 50 ke 40 persen), dan 40 persen itu juga menjadi volume biasanya sampai
dikeraskan lagi.

Untuk mengubah jam atau volumenya, tulis di `config.yaml`, lalu jalankan ulang `waifu run`:

```yaml
voice:
  night:
    from: "23:00"     # mulai jam ini setiap malam
    until: "05:30"    # sampai jam ini (boleh melewati tengah malam)
    level: 30         # paling keras N persen selama mode bisik (10-100)
```

Kalau `briefing.time` lebih awal dari `until`, sapaan paginya ikut berbisik. Begitu juga
pengingat dan pengumuman jadwal: "ingatkan aku jam 5 minum obat" dengan mode bisik menyala
diucapkan paling keras 50 persen. [Alarm bangun](#alarm-bangun) tidak pernah berbisik: untuk
dibangunkan, minta alarm ("bangunkan aku jam 5"), yang makin keras sampai Master bangun. Kalimat
yang mulai diucapkan sebelum jam `from` diucapkan sampai selesai dengan volume biasa.

### Tersimpan dan dicoba

- Volume dan mode bisik disimpan di `data\voice.json` (`voice.state_path`), jadi tetap sesudah
  restart, dan sama untuk `waifu run`, `waifu chat --speak`, dan `waifu say`: yang diubah di satu
  tempat terdengar di tempat lain mulai kalimat berikutnya. Filenya baru dibuat saat volumenya
  pertama kali diubah.
- Semua yang dia ucapkan di PC memakai volume ini, termasuk pengingat, sapaan pagi, dan
  pengumuman lain yang dia mulai sendiri: di 10 persen pengingatnya pun ikut pelan. Pengingat
  yang tidak boleh terlewat sebaiknya juga dikirim lewat
  [notifikasi ke HP](#3-notifikasi-ke-hp-ntfy).
- `waifu say "Halo Master"` diucapkan dengan volume suaranya sekarang (termasuk mode bisik).
  `waifu say --volume 150 "Halo Master"` mencoba volume lain (10-150) tanpa menyimpannya.
- Kalau filenya rusak, file itu dipindah ke `voice.json.corrupt-<waktu>` dan volumenya kembali
  100 persen. Kalau filenya tidak bisa dibaca, dia tetap bicara dengan volume terakhir yang
  terbaca.

`waifu check` menunjukkan volumenya, dan volume yang terdengar sekarang kalau mode bisik sedang
menahannya:

```text
✓ Volume suaraku: 100%, mode bisik mati (22:00-06:00 paling keras 50%)
✓ Volume suaraku: 120%, mode bisik aktif (22:00-06:00 paling keras 50%), sekarang 50%
```

### Di HP

Balasan di [HP](#cyrene-di-hp) diputar oleh HP dengan volume HP, jadi volume suaranya tidak
berpengaruh di sana. "Kecilkan suaramu" yang diketik di HP mengubah suaranya di PC.

Tidak ada yang dikirim ke mana pun: `data\voice.json` hanya berisi volume dan mode bisik.

## Lagu di YouTube

Cyrene bisa memutarkan lagu: dia membuka halaman YouTube lagu itu di browser default PC ini,
tempat lagunya langsung diputar. Fitur ini sudah menyala dari awal (`music.enabled: true`) di
`waifu run` dan `waifu chat`, dengan otak Claude maupun Ollama. Spotify tidak didukung.

```text
Master : "Cyrene, putar lagu Hope Is the Thing With Feathers."
Cyrene : "Sudah kubuka di YouTube, Master. Selamat mendengarkan!"
Master : "Cyrene, puterin playlist lofi buat belajar."
Master : "Cyrene, pause musiknya."             (tombol media, lewat perangkat musik)
```

### Contoh kalimat

- **Memutar:** "Cyrene, putar lagu Hope Is the Thing With Feathers.", "Cyrene, puterin
  playlist lofi buat belajar.", "Cyrene, play Hoyo-Mix Cyrene theme.", "Cyrene, mainkan OST
  Honkai Star Rail."
- **Jeda, lagu berikutnya, dan volume:** "Cyrene, pause musiknya.", "lagu berikutnya",
  "kecilin volumenya", "pindah lagu". Ini tombol media dan volume Windows lewat perangkat musik
  `type: media` (lihat [Musik, volume, dan aplikasi PC](#musik-volume-dan-aplikasi-pc)), jadi
  tambahkan dulu perangkat itu di `config.yaml`. Chrome dan Edge menanggapi tombol media seperti
  pemutar lain. Kalimat seperti ini tidak pernah membuka halaman lagu, juga kalau perangkat
  musik belum ada; hanya kata putar di kalimat yang sama yang meminta lagu ("matiin musik ini,
  puterin lofi aja"), dan tidak pernah bersama kata berikutnya atau sebelumnya ("putar lagu
  berikutnya" tetap tombol media).

### Dengan Claude atau Ollama

- **Dengan otak Claude** dan [pencarian web](#pencarian-web) menyala, dia mencari dulu halaman
  YouTube lagu itu di web, lalu membuka video atau playlist yang tepat. Biasanya cukup satu
  pencarian web, yang biayanya biasanya beberapa sen dolar: 1 sen untuk pencariannya (10 dolar
  per 1000 pencarian) ditambah token hasil pencarian (lihat biaya di
  [Pencarian web](#pencarian-web)), tercatat di `waifu usage`.
- **Dengan Ollama** (atau pencarian web mati), dia tidak bisa mencari link lagu, jadi dia tidak
  pernah diberi link: yang dibuka selalu halaman pencarian YouTube untuk judul itu, dan dia
  bilang begitu. Master yang memilih lagunya. Link yang otaknya karang sendiri tidak pernah
  dibuka; link yang Master ketik di `waifu chat` atau dari HP juga hanya membuka halaman
  pencarian, jadi untuk link pakai [`waifu youtube`](#dari-terminal-waifu-youtube).
- **Dengan Claude**, kalau link yang tepat tidak ketemu, atau link yang dia berikan bukan video
  atau playlist YouTube, yang dibuka juga halaman pencarian YouTube.
- Link tidak pernah dibacakan; dia cukup bilang apa yang dibuka.

### Hanya kalau diminta, hanya YouTube

- Halaman hanya dibuka kalau **kalimat Master di giliran itu juga** lolos pemeriksaan kata ini:
  - Kalimat yang mencari atau bertanya tidak pernah lolos, apa pun kata lainnya: `cari`,
    `carikan`, `cariin`, `nyari`, `nyariin`, `mencari`, `search`, `info`, `berita`, `lirik`,
    `lyrics`, `harga`, `judul`, `penyanyi`, `siapa`, `apa`, `apakah`, `kapan`, `kenapa`,
    `mengapa`, `bagaimana`, `gimana`, `berapa`. Kalimat seperti ini membuatnya membaca web, dan
    di situ `play` sering berarti main game dan `dengerin` berarti mendengarkan orang.
  - Kalimat yang meminta lagu berikutnya atau sebelumnya juga tidak pernah lolos: `next`,
    `berikutnya`, `selanjutnya`, `previous`, `prev`, `sebelumnya`.
  - Selain itu, kalimat lolos kalau memuat kata putar (`putar`, `puterin`, `putarkan`,
    `mainkan`, `mainin`, `setelin`, `play`, `dengerin`), atau menyebut `lagu`, `musik`, `music`,
    `playlist`, atau `youtube` tanpa kata tombol media atau volume (`pause`, `jeda`, `jedain`,
    `stop`, `berhenti`, `hentikan`, `matikan`, `matiin`, `off`, `lanjut`, `lanjutkan`,
    `lanjutin`, `resume`, `skip`, `lewati`, `lewatin`, `pindah`, `volume`, `kecilkan`,
    `kecilin`, `besarkan`, `besarin`, `gedein`, `pelankan`, `pelanin`, `keraskan`, `kerasin`,
    `down`, `up`, `mute`).
  - Kata pencarian, pertanyaan, berikutnya, dan tombol media juga dihitung dengan akhiran `-nya`
    (`liriknya`, `volumenya`); kata putar dan kata lagu tidak, jadi "lagunya bagus ya" hanya
    komentar.

  Jadi "putar lagu ...", "setel lagu yang semangat", dan "aku mau musik" membuka lagu, tapi
  "cari lirik lagu ...", "siapa yang nyanyi lagu ...", "lagu berikutnya", "pindah lagu",
  "setel AC ke 24", dan "kamu dengar aku?" tidak. "Cari lagu lofi terus putar" dan "putar lagu
  apa aja" tetap membuka lagu (kata cari bersama kata putar dan kata lagu berarti "carikan lalu
  putar"; "apa aja" berarti "apa saja"), tapi "sebelum aku play Genshin, cari kode redeem" tidak.
  Permintaan yang juga bertanya, misalnya "lagu apa yang lagi hits? putar", tidak membuka apa pun:
  ulangi tanpa pertanyaannya. Namanya dan kata panggilannya (`assistant.wake_words`)
  tidak dihitung, dan permintaan di kalimat sebelumnya tidak berlaku untuk kalimat berikutnya.
- Kode Cyrene sendiri yang memeriksa kata-kata itu, apa pun keinginan otaknya. Pemeriksaannya
  hanya mencocokkan kata, bukan arti. Di giliran yang kalimatnya tidak lolos (termasuk setiap
  kalimat dengan kata pertanyaan di atas, dan kata pencarian tanpa kata putar dan kata lagu), halaman web yang dia baca tidak bisa
  membuatnya membuka apa pun. Kalimat yang lolos bisa saja hanya komentar atau pertanyaan tanpa
  kata-kata itu ("ada lagu yang enak nggak?"); di giliran seperti itu, sama seperti di
  permintaan lagu, halaman web yang dia baca masih bisa memengaruhi halaman YouTube mana yang
  dibuka, tapi tetap hanya satu halaman YouTube.
- Paling banyak satu halaman untuk setiap kalimat. Kalau otaknya meminta halaman kedua di
  kalimat yang sama, tidak ada yang dibuka lagi, dan itu tidak dihitung gagal.
- **Hanya alamat YouTube yang dibuka:** video (`youtube.com/watch?v=...` atau `youtu.be/...`)
  atau playlist (`youtube.com/playlist?list=...`) lewat `https` di `www.youtube.com`,
  `youtube.com`, `m.youtube.com`, `music.youtube.com`, atau `youtu.be`, dan halaman pencarian
  yang dibuat kode Cyrene sendiri. Situs lain, alamat yang mirip YouTube, Shorts, dan channel
  tidak dibuka; halaman pencarian YouTube dibuka sebagai gantinya.
- **Membuka browser mengambil fokus dari game** yang sedang dimainkan: jendela browser muncul
  di depan, dan game layar penuh bisa ter-minimize. Karena itu dia hanya membukanya kalau
  diminta.
- Dia tidak mengunduh lagu, tidak memutarnya lewat suaranya sendiri, tidak memakai API key
  YouTube, dan tidak mengendalikan browser selain membuka halaman; tab-nya ditutup sendiri.

### YouTube Music

Dengan `music.service: youtube_music`, lagu, playlist, dan pencariannya dibuka di YouTube Music
(`music.youtube.com`), juga link `youtube.com` atau `youtu.be` yang dia temukan.

### Dari terminal: `waifu youtube`

```powershell
waifu youtube https://youtu.be/<kode-video>      # buka video atau playlist YouTube itu
waifu youtube hope is the thing with feathers    # buka halaman pencarian YouTube
```

Link video atau playlist YouTube dibuka langsung, tanpa bagian tambahan seperti `t=` atau
`si=`; kata lain, juga link yang bukan YouTube, membuka halaman pencarian. Di PowerShell, link
yang memuat `&` harus di antara tanda kutip. Perintahnya menampilkan alamat yang dibuka:

```text
✓ Pencarian dibuka di YouTube: https://www.youtube.com/results?search_query=hope+is+the+thing+with+feathers
```

Perintah ini tetap jalan walau `music.enabled: false` (yang dimatikan hanya tool lagu
Cyrene). Kalau Windows gagal membuka browser, perintahnya menampilkan
`✗ Browser tidak bisa dibuka: ...`.

### Mengatur dan mematikannya

`waifu check` menampilkan `✓ Lagu YouTube: aktif (YouTube)` (atau `(YouTube Music)`), atau
`! Lagu YouTube mati`; tidak ada yang dibuka. Bagian ini tidak perlu ditulis untuk memakai
bawaannya; untuk mengubahnya, tulis di `config.yaml` (contohnya ada di `config.example.yaml`):

```yaml
music:
  enabled: true      # false: tanpa tool lagu (`waifu youtube` tetap jalan)
  service: youtube   # youtube | youtube_music
```

Jalankan ulang `waifu run` setelah mengubahnya.

### Privasi lagu

- Dengan Claude, judul lagu yang Master minta menjadi kata kunci pencarian web Anthropic,
  seperti pencarian web lainnya (lihat
  [Privasi pencarian dan promo](#privasi-pencarian-dan-promo)).
- Halamannya dibuka di browser default seperti saat Master membukanya sendiri: YouTube
  melihatnya bersama akun Google yang login di browser itu, dan riwayat tontonannya tercatat di
  sana.
- Cyrene tidak menyimpan lagu yang diminta. Log mencatat kalimat Master (`Heard ...`) dan,
  dengan Claude, setiap tool call beserta argumennya (`Tool play_youtube ...`: judul dan link
  lagunya), seperti tool lainnya.

## Melihat layar

Dengan otak Claude (`brain.backend: claude`), Cyrene bisa melihat layar PC saat Master
memintanya: dia mengambil **satu** tangkapan layar monitor utama, melihatnya, lalu menjawab
tentang isinya. Fitur ini sudah menyala dari awal (`vision.enabled: true`), di `waifu run`
dan `waifu chat`.

```text
Master : "Cyrene, lihat layarku, menurutmu tim ini gimana?"
Cyrene : "Timnya seimbang, Master! Tapi healer-nya levelnya masih rendah."
Master : "Cyrene, ini error apa ya, coba lihat."
Master : "Liat deh build-ku, Cyrene."
```

### Hanya kalau diminta

- Dia hanya melihat kalau **kalimat Master di giliran itu juga** memintanya, yaitu memuat
  salah satu kata ini: `lihat`, `liat`, `lihatin`, `liatin`, `tengok`, `tengokin`, `layar`,
  `layarku`, `screen`, `screenshot`, `monitor`, atau `見て`. Kode Cyrene sendiri yang
  memeriksanya, apa pun keinginan otaknya: tanpa kata itu, tangkapan layarnya ditolak.
  Namanya dan kata panggilannya tidak dihitung, dan permintaan di kalimat sebelumnya tidak
  berlaku lagi untuk kalimat berikutnya.
- Pesan dari HP yang dikirim selama jendela [kamera HP](#kamera-hp) terbuka hanya membuka
  izinnya dengan kata yang menyebut layarnya (`layar`, `layarku`, `screen`, `screenshot`,
  `monitor`): di pesan seperti itu "lihat ini" berarti kamera HP, bukan layar PC.
- Kata-kata itu hanya membuka izinnya untuk kalimat itu. Otaknya diberi tahu untuk melihat
  hanya kalau Master memang memintanya, jadi "aku lagi lihat film" biasanya tidak membuatnya
  melihat layar, tapi itu keputusan model, bukan kode.
- `見て` dihitung kalau terpisah dari kata lain ("キュレネ、見て!"); "これ見て" ditulis Whisper
  tanpa spasi sebagai satu kata, jadi tidak dihitung.
- Paling banyak satu tangkapan layar untuk setiap kalimat, dan hanya monitor utama (monitor
  lain tidak pernah). Dia tidak pernah melihat sendiri, tidak merekam, tidak mengawasi
  terus-menerus, dan tidak mengklik atau mengetik apa pun.
- Dengan Ollama fitur ini tidak ada: qwen3:8b tidak bisa melihat gambar, jadi tool-nya tidak
  ditawarkan sama sekali.

### Privasi layar

- **Apa pun yang tampil di monitor utama ikut terkirim** ke API Anthropic sebagai bagian dari
  percakapan: chat, email, nama akun, notifikasi. Tutup atau kecilkan dulu yang pribadi
  sebelum memintanya melihat. Dia diminta tidak membacakan detail pribadi (pesan, email,
  nomor akun) kecuali Master menanyakannya, dan tidak menyimpan apa pun yang dilihatnya di
  layar ke ingatannya kecuali Master memintanya.
- Gambarnya tetap ada di riwayat sesi itu dan ikut terkirim lagi bersama giliran berikutnya
  sampai sesinya berakhir (10 menit tanpa percakapan atau 40 giliran), seperti isi obrolan
  lainnya. Jadi dia masih bisa menjawab pertanyaan lanjutan tentang gambar yang sama tanpa
  melihat lagi. Setiap gambar juga menambah biaya API.
- Karena setiap giliran mengirim ulang semua gambar sesi itu, sesi baru dimulai sendiri
  begitu gambar di riwayatnya mencapai 10 MB, kira-kira 3 sampai 6 tangkapan layar game
  yang ramai (layar desktop atau tulisan jauh lebih kecil; foto [kamera HP](#kamera-hp) ikut
  dihitung). Setelah itu dia tidak ingat lagi gambar dan obrolan sebelumnya. Kalau dia sedang
  menunggu jawaban "iya" (misalnya untuk mematikan komputer), sesinya menunggu satu giliran
  lagi supaya jawabannya sampai; hanya kalau gambarnya sudah 20 MB sesinya tetap berganti, dan
  pertanyaannya ikut hilang: "iya" itu tidak menjalankan apa pun, jadi minta lagi perintahnya.
- Tangkapan layarnya **tidak pernah disimpan ke disk dan tidak ditulis ke log**, juga dengan
  `-v` dan `--log-file`; log hanya mencatat ukurannya.

### Maskot Cyrene ikut tertangkap

Kalau avatar menyala, maskotnya (di pojok kanan bawah, selalu di atas) ikut masuk ke
tangkapan layar. Cyrene diberi tahu bahwa sosok anime di pojok itu dirinya sendiri, jadi
dia tidak menilainya sebagai bagian dari layar Master. Kalau bagian layar di belakang
maskot penting (misalnya error di pojok kanan bawah), geser dulu maskotnya, pindahkan
pojoknya dengan `avatar.corner`, atau sembunyikan dengan **×**.

### Game layar penuh

Game dengan mode layar penuh eksklusif (*fullscreen*) bisa tertangkap hitam. Pilih mode
**borderless** (*windowed fullscreen*) atau *windowed* di pengaturan grafis gamenya. Kalau
tangkapan layarnya hitam semua, Cyrene sendiri akan menyarankannya.

### Mengatur dan mematikannya

`waifu check` menampilkan `✓ Melihat layar: aktif (Claude)`,
`! Melihat layar: tidak tersedia dengan Ollama`, atau `! Melihat layar mati`. Untuk
mematikannya, tulis di `config.yaml` (contohnya ada di `config.example.yaml`):

```yaml
vision:
  enabled: false
  max_side: 1280   # sisi terpanjang tangkapan layar dalam piksel (512-1568)
```

`max_side` mengecilkan tangkapan layar (perbandingannya tetap, tidak pernah diperbesar)
sampai sisi terpanjangnya sebesar itu: lebih kecil berarti lebih hemat, tapi tulisan kecil
lebih sulit dibaca. API Anthropic menerima paling besar 5 MB per gambar; di atas sekitar
1450, layar game yang sangat ramai bisa melewatinya, dan Cyrene otomatis mengambilnya sekali
lagi dengan ukuran lebih kecil. Display scaling Windows (125%, 150%) tidak berpengaruh:
seluruh layar tetap tertangkap. Jalankan ulang `waifu run` setelah mengubahnya.

## Artinya apa

Copy teks bahasa Jepang atau Inggris (dialog game, halaman web, tweet), lalu tanya Cyrene: dia
membaca clipboard PC, mengucapkan intinya, dan menampilkan terjemahan lengkapnya di
[kartu teks](#kartu-teks). Dengan otak Claude maupun Ollama, di `waifu run`, `waifu chat`, dan
dari HP; sudah menyala dari awal, tanpa paket tambahan.

```text
(Master meng-copy 「お疲れ様でした！また明日ね」)
Master : "Cyrene, artinya apa yang kucopy?"
Cyrene : "Artinya kurang lebih terima kasih atas kerja kerasnya, sampai besok, Master."
         [kartu Terjemahan: terjemahan lengkap, cara baca kana dan romaji, daftar kata]
Master : "Simpan kata itu."                        (tanpa nama: jendela lanjutan terbuka)
Master : "Cyrene, terjemahin yang barusan kucopy."  (teks bahasa Inggris)
```

Terjemahan satu kalimat pendek cukup diucapkan, tanpa kartu. Untuk teks bahasa Jepang, kartunya
memuat terjemahan lengkap, cara bacanya dengan kana dan romaji, dan untuk frasa pendek daftar
katanya (kata, cara baca, arti). Yang diucapkan tetap tanpa romaji.

### Ctrl+Alt+T

Di tengah game, tekan **Ctrl+Alt+T**: sama dengan bertanya "Artinya apa teks yang kucopy?" di PC,
tanpa mic, lalu jendela lanjutan terbuka seperti biasa (jadi "simpan kata itu" cukup tanpa nama).
Selagi dia mendengarkanmu, berpikir, atau bicara, tombolnya diabaikan tanpa suara, sama seperti
Ctrl+Alt+R: tekan Ctrl+Alt+X dulu untuk memotongnya, lalu Ctrl+Alt+T. Jalan juga selama dia tidak
mendengarkan (Ctrl+Alt+Q), mode fokus, dan mode game. Untuk mengganti atau mematikannya
(`hotkeys.translate`), lihat [Hotkey global](#hotkey-global).

### Hanya kalau diminta

- Dia hanya membaca clipboard kalau **kalimat Master di giliran itu juga** menyebut yang
  dicopy, yaitu memuat salah satu kata ini: `copy`, `copas`, `kopas`, `copyan`, `kucopy`,
  `dicopy`, `kukopi`, `dikopi`, `salin`, `kusalin`, `disalin`, `salinan`, `clipboard`,
  `klipboard`, `copied`, atau `コピー`. Kode Cyrene sendiri yang memeriksanya, apa pun keinginan
  otaknya; namanya dan kata panggilannya tidak dihitung. Paling banyak sekali untuk setiap
  kalimat.
- Dia tidak pernah membaca clipboard sendiri: tidak mengawasinya, tidak menerjemahkan setiap
  copy, tidak di sapaan pagi atau hal lain yang dia mulai sendiri, dan tidak karena isi halaman
  web atau isi teks yang dicopy. Dia juga tidak pernah mengubah isi clipboard.
- Teks yang dicopy dianggap data dari aplikasi lain, bukan perintah Master: perintah, tautan,
  atau permintaan di dalamnya tidak dijalankan.
- Hanya teks: gambar atau file yang dicopy tidak dibaca ("yang dicopy bukan teks"). Hanya 2000
  karakter pertama yang dibaca, dan dia bilang kalau teksnya terpotong.
- Pesan dari [HP](#cyrene-di-hp) juga membaca clipboard **PC**, bukan clipboard HP.
- Clipboard yang sedang dipegang aplikasi lain dicoba lima kali; kalau masih dipakai, dia minta
  Master mencoba lagi.

### Yang tidak dibaca

- Teks yang kelihatan seperti rahasia ditolak utuh: tidak dibacakan, tidak dikirim ke otaknya,
  dan tidak ditulis ke log. Yaitu satu kata 8-128 huruf yang mencampur huruf besar, huruf kecil,
  angka, atau simbol (kata sandi), 20 huruf dan angka berturut-turut (token, API key), 4-8 angka
  (kode OTP atau PIN), nomor kartu, nomor rekening atau HP (hanya angka, 10 atau lebih), kunci
  privat, dan kata seperti "password" atau "PIN" beserta isinya. Dia hanya bilang kelihatannya
  rahasia, tanpa menebak isinya.
- Salinan dari password manager yang menandainya sebagai privat (misalnya KeePass) tidak dibaca
  sama sekali.
- Karena itu teks biasa yang memuat 20 huruf dan angka berturut-turut di mana saja (misalnya link
  panjang) juga ditolak utuh. Copy bagian teksnya saja.

### Privasi artinya apa

- Teks yang dicopy hanya dibaca di giliran yang memintanya. Dengan Claude, teks itu dikirim ke API
  Anthropic sebagai bagian percakapan dan tetap di riwayat sesi itu seperti obrolan lain; dengan
  Ollama, semuanya lokal.
- Tidak pernah ditulis ke disk atau ke log, juga dengan `-v`, `--log-file`, dan `waifu chat -v`:
  log hanya mencatat panjangnya. Dia tidak menyimpannya ke ingatannya kecuali Master memintanya.
- Ditutup di [mode tamu](#mode-tamu).

### Batasan artinya apa

- Ctrl+Alt+T selagi dia sibuk diabaikan tanpa suara.
- Selama `waifu run` jalan, Ctrl+Alt+T tidak sampai ke program lain, misalnya ™ di Word, dan di
  keyboard dengan AltGr, AltGr+T tidak mengetik hurufnya. Ganti tombolnya atau matikan dengan
  `hotkeys.translate: null`.
- Kalau `config.yaml` lama sudah memakai T untuk hotkey lain dan `translate` belum ditulis,
  Ctrl+Alt+T mati dengan peringatan di log; tulis `translate:` dengan tombol lain.
- Dengan Ollama, mutu terjemahan, kana, dan romajinya sebatas model lokalnya.

## Sapaan pagi dan ringkasan harian

Sekali sehari, saat `waifu run` menyala pertama kali di antara jam 07.00 dan 12.00, Cyrene
menyapa Master sendiri dan merangkum harinya dalam 2-4 kalimat pendek: cuaca, jadwal dan
pengingat hari ini, [tugas](#daftar-tugas) yang tenggatnya hari ini atau sudah lewat, game
kalau [teman main gacha](#teman-main-gacha-hoyoverse) menyala, dan hal dari
[ingatannya](#ingatan-cyrene) yang penting hari ini (ulang tahun, ujian), lalu di akhir
[kata Jepang hari ini](#belajar-bahasa-jepang) kalau belajar bahasa Jepang menyala. Data yang
sama, tanpa kata Jepangnya, juga dipakai kapan saja kalau Master bertanya. Fitur ini sudah menyala dari awal
(`briefing.enabled: true`), tapi cuacanya baru ada setelah kotanya diisi (lihat
[Mengatur kota](#mengatur-kota)).

```text
(07.05)  Cyrene : "おはよう, Master! Bandung pagi ini berawan, 21 derajat, nanti paling panas
                   28 dan peluang hujannya 85 persen, jadi bawa payung ya. Jam tiga sore ada
                   pengingat minum obat, laporan tenggatnya hari ini, dan hari ini Master
                   ujian. Semangat!"
Master : "Makasih ya."                    (tanpa memanggil nama: jendela lanjutan terbuka)
Master : "Cyrene, cuaca hari ini gimana?"
Master : "Cyrene, hari ini ada jadwal apa?"
Master : "Cyrene, ringkasan hari ini dong."
```

### Kapan dia menyapa

- **Sekali sehari, di jendela jamnya.** Jendelanya `briefing.time` sampai sebelum
  `briefing.until` (bawaan 07.00-12.00, jam PC), dicek setiap 30 detik selama `waifu run`
  menyala. PC dinyalakan jam 08.30: dia menyapa begitu siap mendengarkan. Dinyalakan jam
  13.00: hari itu tidak ada sapaan.
- **Tidak pernah dua kali.** Hari sapaan terakhir ditulis ke `data\briefing.json` sebelum
  dia mulai menyapa, jadi restart, PC yang dinyalakan lagi pagi itu, atau error di tengah
  sapaan tidak membuatnya menyapa lagi. Sapaan yang gagal tidak diulang hari itu (lihat poin
  terakhir). File yang rusak dipindah ke `briefing.json.corrupt-<waktu>` dan dia mulai dari
  awal. Untuk mendengar sapaannya lagi hari ini (misalnya untuk mengujinya), hapus
  `data\briefing.json` lalu jalankan ulang `waifu run` di dalam jendela jamnya. Menggeser
  `time` atau `until` saja tidak cukup: hari yang sudah disapa tetap tercatat.
- **Tidak pernah memotong.** Seperti pengingat, sapaan diucapkan di sela kalimat Master:
  tidak saat dia menjawab, tidak saat Master sedang bicara, tidak selama jendela lanjutan
  sesudah jawabannya masih terbuka (Master mungkin masih membalas, misalnya menjawab "iya"
  untuk mematikan PC), dan tidak saat Master baru saja memotongnya dengan klik dua kali
  (sapaannya menunggu sampai Master bicara atau jendela lanjutan lewat). Pengingat yang antre
  bersamaan diucapkan dulu, baru sapaannya sesudah jeda singkat.
- **Tidak terlambat.** Sapaan yang masih tertahan sampai `briefing.until` (blok fokus, obrolan
  lewat HP, atau obrolan panjang dengannya) dilewati hari itu, tidak diucapkan siang-siang dan
  tidak dikirim ke HP. Log mencatatnya dengan `Not saying a request held past`.
- **Kalimatnya dari otaknya sendiri** (Claude atau Ollama), jadi berbeda setiap hari dan
  butuh beberapa detik untuk dipikirkan. Sesudahnya jendela lanjutan terbuka: Master bisa
  langsung membalas tanpa memanggil namanya.
- **Ke HP juga**: teks sapaan yang sama dikirim lewat ntfy kalau `notify.enabled: true` dan
  `briefing.push: true` (bawaan; lihat [Notifikasi ke HP](#3-notifikasi-ke-hp-ntfy)).
  Pengirimannya berjalan di belakang, jadi dia langsung mendengarkan lagi walau ntfy lambat.
- **Hanya selama `waifu run`.** `waifu chat` punya ringkasan yang sama untuk ditanyakan,
  tapi tidak menyapa sendiri. `waifu run --dry-run` juga menyapa dan mencatat harinya di file
  yang sama, tapi jadwalnya jadwal sementara yang kosong.
- **Kalau cuaca gagal dibaca** (server cuaca tidak bisa dihubungi atau terlalu lama menjawab,
  kota tidak ditemukan), dia tetap menyapa, bilang singkat bahwa cuacanya tidak bisa dicek,
  lalu menyampaikan sisanya. Itu berlaku selama otaknya sendiri jalan: dengan Ollama (lokal)
  dia tetap menyapa walau internet mati.
- **Kalau otaknya gagal** (dengan Claude: internet mati, Wi-Fi belum tersambung saat PC baru
  menyala, server Claude sibuk atau error), dia tidak menyapa hari itu: dia diam, tidak
  mengucapkan permintaan maaf yang tidak diminta, dan tidak ada yang dikirim ke HP. Log
  menampilkan `Not saying the answer to a request`. Hanya kalau otaknya gagal di tengah
  sapaan, sesudah ringkasan hari itu terbaca, permintaan maafnya masih terucap dan terkirim.

Isi cuacanya: kondisi dan suhu sekarang, suhu terendah dan tertinggi hari ini, peluang hujan
tertinggi hari ini, dan kondisi besok jam 07.00. Prakiraan hari-hari berikutnya ada di
[Cuaca beberapa hari](#cuaca-beberapa-hari); berita, lalu lintas, atau kalender di luar jadwal
Cyrene tidak ada.

### Mengatur kota

Cuaca diambil dari [Open-Meteo](https://open-meteo.com/): gratis, tanpa akun atau API key,
tapi **butuh internet**. Tulis kotamu di `config.yaml`:

```yaml
briefing:
  city: Bandung
```

Nama kota dicari sekali di Open-Meteo saat cuaca pertama kali dibutuhkan, dan hasil pertama
yang dipakai (`waifu briefing` menampilkan nama dan provinsinya). Kalau yang ditemukan kota
lain dengan nama sama, atau kamu ingin titik yang persis, pakai koordinat. Isi keduanya;
koordinat menang atas `city`: cuacanya diambil untuk koordinat itu tanpa mencari kotanya,
dan `city` tetap dipakai sebagai nama tempat yang dia sebut (tanpa `city`, nama tempatnya
angka koordinat). Jadi pastikan keduanya menunjuk tempat yang sama. Di Google Maps, klik
kanan titiknya: angka pertama latitude, angka kedua longitude.

```yaml
briefing:
  enabled: true
  time: "07:00"      # sapaan paling cepat jam ini
  until: "12:00"     # dan sebelum jam ini
  city: Bandung      # nama tempat yang dia sebut
  latitude: -6.9175
  longitude: 107.6191
  push: true         # kirim sapaannya juga ke HP (kalau notify menyala)
  state_path: data/briefing.json
```

Tulis jamnya dengan tanda kutip (`"07:00"`), dan `time` harus sebelum `until`. Contoh
lengkapnya ada di `config.example.yaml`. Jalankan ulang `waifu run` setelah mengubahnya.

### Mengecek cuaca: `waifu briefing`

`waifu briefing` menampilkan tanggal, cuaca, dan jadwal yang dibaca Cyrene untuk sapaannya
(tugasnya dilihat dengan `waifu todo`), tanpa otak (tidak memakai Claude atau Ollama), jadi
cocok untuk menguji kota dan internetnya:

```text
Ringkasan hari ini: Rabu, 14-10-2026
✓ Cuaca di Bandung, Jawa Barat: berawan, 21.4°C; hari ini 18.6-27.9°C, peluang hujan 85%; besok pagi hujan
Jadwal hari ini:
  15:00  Master, sudah jam tiga, waktunya minum obat!
  23:00  rutinitas tidur
```

Kalau cuaca gagal, baris `✗ Cuaca: ...` menyebut sebabnya (kota tidak ditemukan, server tidak
bisa dihubungi atau terlalu lama menjawab, kota belum diisi), sisanya tetap ditampilkan, dan
exit code-nya 1. Satu pengecekan cuaca, pencarian kota dan ramalannya bersama, dibatasi 8
detik. Kalau jadwal tidak bisa dibaca, baris `✗ Jadwal: ...` muncul dan exit code-nya juga 1.
Perintah ini juga jalan walau `briefing.enabled: false`.

`waifu check` menampilkan `✓ Sapaan pagi: jam 07:00-12:00, cuaca untuk Bandung` (dengan
koordinat: `cuaca untuk -6.9175, 107.6191`, atau `cuaca untuk Bandung (-6.9175, 107.6191)`
kalau `city` juga diisi), `! Sapaan pagi: cuaca belum diatur (isi briefing.city)`, atau
`! Sapaan pagi mati`, tanpa menghubungi Open-Meteo.

### Mematikannya

```yaml
briefing:
  enabled: false   # tanpa sapaan pagi dan tanpa ringkasan harian
```

Untuk tetap disapa tapi tanpa notifikasi HP, cukup `push: false`. [Peringatan
hujan](#peringatan-hujan) dan [cuaca beberapa hari](#cuaca-beberapa-hari) tetap jalan selama
kotanya diisi.

## Peringatan hujan

Selama `waifu run` menyala, Cyrene memperingatkan sendiri sebentar sebelum hujan, supaya
jemuran sempat diangkat:

```text
(14.05)  Cyrene : "Sayang, sekitar jam tiga sore kemungkinan hujan di Cianjur, angkat jemuran ya."
```

Kalimat yang sama juga dikirim ke HP kalau [notifikasi](#3-notifikasi-ke-hp-ntfy) menyala.
Fitur ini sudah menyala dari awal (`rain_alert.enabled: true`) dan memakai tempat yang sama
dengan [sapaan pagi](#sapaan-pagi-dan-ringkasan-harian): selama `briefing.city` (atau
koordinatnya) belum diisi, dia diam (lihat [Mengatur kota](#mengatur-kota)). Peringatannya
tetap jalan walau sapaan paginya dimatikan (`briefing.enabled: false`), selama kotanya diisi.

### Kapan dia memperingatkan

- **Sebelum hujan mulai.** Setiap 15 menit dia membaca prakiraan per jam dari Open-Meteo, dan
  memperingatkan kalau hujan diperkirakan mulai dalam `lead_minutes` ke depan (bawaan 60
  menit). Satu jam dihitung hujan kalau peluang hujannya paling sedikit `threshold` (bawaan
  60%), curahnya paling sedikit 0,5 mm, atau kodenya hujan, hujan lokal, atau badai petir.
  Gerimis saja tidak dihitung, kecuali peluang atau curahnya cukup. Peluang hujan saja di jam
  yang sedang berjalan belum dianggap hujan yang turun, jadi hujan sesudahnya tetap
  diperingatkan.
- **Jamnya bulat.** Prakiraannya per jam, jadi dia menyebut jam bulat ("sekitar jam tiga
  sore"): jam hujannya mulai. Open-Meteo mencatat hujan untuk satu jam *sebelum* jam yang
  tertulis (angka jam 16.00 adalah hujan dari 15.00 sampai 16.00), jadi dia menyebut jam 15.00
  dan menghitung `lead_minutes` sampai jam itu. Hujan bisa saja datang lebih cepat dari
  prakiraannya; untuk jarak yang lebih aman, naikkan `lead_minutes` (misalnya 90).
- **Sekali untuk satu hujan.** Dia tidak mengulang saat hujannya makin dekat, saat peluangnya
  naik-turun di sekitar batas, saat hujan sedang turun, atau saat hujannya berhenti sebentar.
  Hujan berikutnya baru diperingatkan sesudah paling sedikit tiga jam kering di prakiraan.
  Hujan yang sudah turun saat dia mulai menyala tidak diperingatkan.
- **Hanya di jam aktif**, di antara `active_from` dan `active_until` (bawaan 06.00-21.00, jam
  PC). Di luar jam itu dia tidak membaca prakiraan dan diam, jadi malam hari tidak ada
  peringatan.
- **Tidak pernah memotong.** Seperti sapaan pagi, peringatannya diucapkan di sela kalimat
  Master: tidak saat dia menjawab, tidak saat Master bicara, dan tidak selama jendela
  lanjutan sesudah jawabannya masih terbuka. Kalau Master sedang ngobrol dengannya,
  peringatannya menunggu sampai obrolannya selesai; notifikasi HP-nya langsung dikirim.
- **Kalau internet mati** atau Open-Meteo tidak menjawab, dia tidak bilang apa-apa. Log
  mencatatnya sekali (`Could not read the rain forecast`), dan 15 menit kemudian dia mencoba
  lagi.
- **Hanya selama `waifu run`.** `waifu chat` tidak memperingatkan. Hujan yang sudah
  diperingatkan hanya diingat selama `waifu run` berjalan, jadi sesudah restart hujan yang
  sama bisa diperingatkan sekali lagi.

### Pengaturan

```yaml
rain_alert:
  enabled: true
  lead_minutes: 60       # beri tahu sekian menit sebelum hujan mulai (30-180)
  threshold: 60          # peluang hujan per jam (%) yang dihitung sebagai hujan (30-100)
  active_from: "06:00"   # hanya mulai jam ini (jam PC)
  active_until: "21:00"  # dan sebelum jam ini
  push: true             # kirim peringatannya juga ke HP (kalau notify menyala)
```

Kotanya ditulis di bagian `briefing` (lihat [Mengatur kota](#mengatur-kota)). Yang dia sebut
adalah `city` seperti yang kamu tulis ("di Cianjur"); dengan koordinat saja, tanpa `city`,
dia tidak menyebut tempat. Tulis jamnya dengan tanda kutip, dan `active_from` harus sebelum
`active_until`. Kalau peringatannya terlalu sering, naikkan `threshold` (misalnya 70 atau 80);
jam yang kodenya hujan atau badai petir tetap dihitung. Untuk mematikannya, `enabled: false`;
untuk tanpa notifikasi HP, `push: false`. Jalankan ulang `waifu run` setelah mengubahnya.

### Mengecek prakiraan: `waifu rain`

`waifu rain` menampilkan kapan hujan berikutnya dalam 24 jam, dibaca seperti peringatannya
membacanya (dengan `rain_alert.threshold`), tanpa otak. Cocok untuk membandingkannya dengan
aplikasi cuaca di HP:

```text
Prakiraan hujan di Cianjur, Jawa Barat, per 14:15 (peluang 60% ke atas):
Hujan berikutnya: sekitar jam tiga sore (15:00-16:00, peluang 85%).
```

"per 14:15" adalah waktu prakiraannya. Di dalam kurung ada jam mulai dan selesainya hujan
(dengan `besok` di depan jam yang jatuh besok) dan peluang tertingginya.
Saat hujan sedang turun ada baris `Sekarang sedang hujan.`; tanpa hujan,
`Tidak ada hujan dalam 24 jam ke depan.` Kalau kota belum diisi atau cuaca gagal dibaca,
baris `✗ ...` menyebut sebabnya dan exit code-nya 1. Perintah ini juga jalan walau
`rain_alert.enabled: false`.

`waifu check` menampilkan
`✓ Peringatan hujan: 60 menit sebelumnya, peluang 60% ke atas, jam 06:00-21:00, untuk Cianjur`,
`! Peringatan hujan: cuaca belum diatur (isi briefing.city)`, atau `! Peringatan hujan mati`,
tanpa menghubungi Open-Meteo.

## Cuaca beberapa hari

Selain cuaca hari ini, Cyrene menjawab prakiraan untuk sisa hari ini, nanti malam, besok, akhir
pekan, sampai enam hari lagi, atau satu bagian hari:

```text
Master : "Cyrene, besok cuaca gimana?"
Cyrene : "Besok di Cianjur paginya berawan, siangnya kemungkinan hujan sekitar 80 persen,
          suhunya 19 sampai 28 derajat."
Master : "Nanti sore hujan nggak?"
Master : "Akhir pekan hujan nggak?"
Master : "Jumat cuacanya gimana?"
```

Tempatnya sama dengan [sapaan pagi](#sapaan-pagi-dan-ringkasan-harian) dan
[peringatan hujan](#peringatan-hujan) (lihat [Mengatur kota](#mengatur-kota)). Fitur ini ada
selama `briefing.city` (atau koordinatnya) diisi, juga kalau sapaan paginya dimatikan
(`briefing.enabled: false`); tidak ada pengaturan sendiri.

### Yang bisa ditanyakan

- **Sisa hari ini:** "nanti hujan nggak?" dibaca dari sekarang sampai tengah malam. Cuaca
  sekarang dan hari ini secara keseluruhan ("cuaca hari ini gimana?") tetap dari
  [ringkasan hari](#sapaan-pagi-dan-ringkasan-harian). Kalau sapaan paginya dimatikan
  (`briefing.enabled: false`), ringkasan hari tidak ada, jadi pertanyaan itu dijawab dari
  prakiraan sisa hari ini.
- **Bagian hari:** pagi (04.00-11.00), siang (11.00-15.00), sore (15.00-19.00), dan malam
  (19.00-24.00), hari ini atau hari lain: "nanti sore hujan?", "besok pagi cerah nggak?",
  "Jumat malam hujan nggak?". Bagian hari ini yang sudah lewat ("nanti sore" ditanya jam 20.00)
  dia bilang sudah lewat.
- **Besok dan tanggal lain:** hari ini dan enam hari sesudahnya. Untuk hari yang lebih jauh dia
  bilang prakiraannya hanya sampai hari keenam.
- **Akhir pekan:** Sabtu dan Minggu berikutnya; pada hari Sabtu, sisa hari ini dan Minggu; pada
  hari Minggu, sisa hari ini.

Untuk satu hari dia membaca kondisi terparahnya, suhu terendah dan tertinggi, peluang hujan
tertinggi, dan curah hujannya, ditambah kondisi dan peluang hujan tiap bagian hari, jadi "besok
hujan jam berapa?" terjawab sekali tanya. Angka bagian hari dihitung dari prakiraan per jam,
yang mencatat hujan untuk satu jam *sebelum* jam yang tertulis, sama seperti peringatan hujan.

- **Setiap pertanyaan menunggu Open-Meteo** (butuh internet, paling lama 8 detik, dia diam
  sebentar). Kalau gagal, dia bilang singkat bahwa cuacanya tidak bisa dicek.
- **Tanggalnya tanggal tempat itu:** tepat sesudah tengah malam, "besok" berarti hari sesudah
  tanggal yang baru.
- **Hanya kalau Master bertanya.** Sapaan pagi dan hal lain yang dia mulai sendiri tidak
  memakainya, jadi sapaan paginya tetap seperti sebelumnya.
- **Nama tempat** yang dia sebut adalah `city` seperti yang kamu tulis ("di Cianjur"); dengan
  koordinat saja, tanpa `city`, dia tidak menyebut tempat.
- Prakiraan hari yang jauh lebih kasar daripada besok.

### Mengecek prakiraan: `waifu cuaca`

`waifu cuaca` menampilkan sisa hari ini; `waifu cuaca besok`, `waifu cuaca akhir-pekan`, atau
`waifu cuaca 2026-10-30` hari lain, tanpa otak, dibaca sama seperti yang dia jawab:

```text
> waifu cuaca besok
Prakiraan cuaca di Cianjur, Jawa Barat, per 20:15:
Minggu 25-10-2026: hujan, 18.9-28.3°C, peluang hujan 80%, curah hujan 7.5 mm
  pagi: berawan, 19-24.5°C, peluang hujan 10%, curah hujan 0 mm
  siang: hujan, 25-27.5°C, peluang hujan 80%, curah hujan 7.5 mm
  sore: berawan, 26-28°C, peluang hujan 20%, curah hujan 0 mm
  malam: berawan, 20-24°C, peluang hujan 10%, curah hujan 0 mm
```

Bagian hari ini yang sudah lewat tidak ditampilkan, dan nilai yang tidak diketahui Open-Meteo
dilewati. Kalau kota belum diisi, cuaca gagal dibaca, atau tanggalnya di luar tujuh hari, baris
`✗ ...` menyebut sebabnya dan exit code-nya 1. Perintah ini juga jalan walau
`briefing.enabled: false`.

### Mode tamu dan privasi

Prakiraannya tetap bisa ditanyakan saat [mode tamu](#mode-tamu): cuaca kota yang sudah disebut
bukan hal pribadi. Setiap pertanyaan mengirim tempatnya (koordinat, atau nama kota yang dicari
sekali) ke Open-Meteo, seperti untuk sapaan pagi; dengan Claude, hasilnya (nama kota, tanpa
koordinat) ikut ke API Anthropic seperti obrolan biasa. Tidak ada yang disimpan ke disk.

## Biaya API Claude

Dengan otak Claude (`brain.backend: claude`), Cyrene mencatat pemakaian setiap jawaban dari
API dan menghitung perkiraan biayanya, jadi Master bisa bertanya kapan saja berapa yang sudah
terpakai. Kalau anggaran bulanan diisi, dia memberi tahu sendiri saat biayanya mendekati dan
melewati anggaran itu. Fitur ini sudah menyala dari awal (`usage.enabled: true`), di
`waifu run` dan `waifu chat` (juga dengan `--dry-run`, yang tetap memakai API sungguhan).
Dengan Ollama tidak ada yang dicatat: otaknya lokal dan gratis.

```text
Master : "Cyrene, biaya API bulan ini berapa?"
Cyrene : "Sekitar 89 sen dolar bulan ini, Master, dari 59 permintaan. Itu perkiraanku sendiri ya."
Master : "Cyrene, hari ini sudah habis berapa?"
Master : "Cyrene, kalau dirupiahkan berapa?"           (perlu usage.idr_per_usd)
```

Kalau dia mencari di web pada hari atau bulan itu, dia juga tahu berapa kali dia mencari dan
berapa biaya pencariannya. Biaya itu sudah termasuk dalam totalnya, bukan tambahan. Yang
dicatat hanya jumlah pencariannya, jadi biaya pencarian itu dihitung dengan harga
`usage.web_search_per_1000_usd` yang sekarang; kalau harganya pernah diubah, totalnya tetap
memakai harga saat pencarian terjadi, dan biaya pencariannya tidak pernah melebihi total.

### Yang dicatat dan yang tidak

- **Dicatat:** setiap jawaban Claude untuk Cyrene di PC ini, termasuk setiap putaran tool
  (satu kalimat Master bisa jadi beberapa permintaan) dan jawaban yang ditolak. Kalau model
  utama menolak di tengah jalan lalu model pengganti menjawab (`fallbacks: true`), keduanya
  ditagih, jadi keduanya dicatat sebagai permintaan sendiri-sendiri. Dari setiap jawaban
  dicatat jumlah token yang dilaporkan API: input, output, tulis cache, dan baca cache,
  serta jumlah pencarian web (10 dolar per 1000 pencarian, `usage.web_search_per_1000_usd`).
  Pencarian promo AI mingguan juga tercatat (lihat
  [Pencarian web dan promo gratis](#pencarian-web-dan-promo-gratis)).
  Totalnya disimpan per hari (jam PC) dan per model di `data\usage.json`.
- **Harganya perkiraan.** Biaya dihitung dari harga resmi per sejuta token yang dibawa
  Cyrene, untuk `claude-opus-5-5`: input 4 dolar, output 20 dolar, tulis cache 5 dolar (cache
  5 menit yang otomatis dipakainya, 1,25 kali harga input), dan baca cache 0,20 dolar. Harga
  bisa berubah;
  kalau berubah, tulis harga barunya di `usage.prices` (lihat
  [Anggaran, kurs, dan harga](#anggaran-kurs-dan-harga)). Jawaban dari model yang tidak
  ada di tabel harga (misalnya model pengganti dari `fallbacks: true` saat model utama
  menolak) dihitung dengan harga `usage.default_model` dan ditandai `(perkiraan)`.
- **Tidak terlihat olehnya:** aplikasi atau orang lain yang memakai API key yang sama,
  program lain di PC ini, dan tagihan di Anthropic Console itu sendiri (pajak, kredit,
  pembulatan). Permintaan yang gagal sebelum ada jawaban (internet putus) juga tidak
  tercatat. Untuk angka tagihan yang pasti, lihat halaman Usage dan Billing di Console.
- Dia tidak pernah berhenti sendiri karena biaya: anggaran hanya membuatnya memberi tahu.

### Melihat biaya: `waifu usage`

`waifu usage` menampilkan bulan ini hari demi hari, `waifu usage --month 2026-09` bulan lain:

```text
Biaya API Claude Oktober 2026, dari catatan Cyrene sendiri:
TANGGAL     PERMINTAAN  INPUT  OUTPUT  CACHE TULIS  CACHE BACA  USD    IDR
12-10-2026  18          7560   5580    16200        252000      $0.27  Rp4.454
13-10-2026  31          11780  8990    21700        511500      $0.44  Rp7.135
14-10-2026  10          5000   3550    10800        150000      $0.17  Rp2.852   (perkiraan)
Total       59          24340  18120   48700        913500      $0.89  Rp14.441  (perkiraan)
(perkiraan): sebagian respons datang dari model yang tidak ada di tabel harga, jadi dihitung dengan harga claude-opus-5-5.
```

Angka di kolom token adalah jumlah token. Kolom IDR hanya muncul kalau `usage.idr_per_usd`
diisi, dan kolom `PENCARIAN WEB` hanya di bulan yang ada pencarian webnya. Bulan tanpa catatan
menampilkan `Belum ada catatan biaya.` Perintah ini hanya membaca catatannya, tanpa
menghubungi API, jadi jalan dengan otak apa pun dan walau
`usage.enabled: false`, juga selagi `waifu run` berjalan.

`waifu check` menampilkan `✓ Biaya API: $0.89 bulan ini (59 permintaan)`, atau
`! Biaya API dicatat hanya dengan backend Claude` (dengan Ollama), atau
`! Pencatatan biaya mati`.

### Anggaran, kurs, dan harga

```yaml
usage:
  monthly_budget_usd: 20   # anggaran per bulan kalender, dalam dolar
  idr_per_usd: 16300       # kurs rupiah per dolar, untuk ikut menyebut biayanya dalam rupiah
  prices:                  # dolar per sejuta token; menimpa atau menambah harga bawaan
    claude-opus-5-5: {input: 4.00, output: 20.00, cache_write: 5.00, cache_read: 0.20}
  web_search_per_1000_usd: 10   # pencarian web Claude: dolar per 1000 pencarian, di luar token
```

- **Anggaran:** dengan `monthly_budget_usd`, sekali saat biaya bulan itu melewati 80% dan
  sekali saat melewati 100%, dia memberi tahu sendiri dengan wajah kaget, misalnya "Master,
  biaya API bulan ini sekitar 16,05 dolar, sudah mencapai 80 persen dari anggaran 20 dolar."
  Kalimatnya menunggu obrolan berhenti sejenak: tidak pernah memotong Master atau jawabannya
  sendiri, dan baru diucapkan setelah jendela lanjutan sesudah jawaban itu lewat, jadi Master
  tetap bisa membalas jawabannya (misalnya "iya") tanpa memanggil namanya. Kalimat yang sama
  dikirim juga ke HP kalau notifikasi menyala (lihat
  [Notifikasi ke HP](#3-notifikasi-ke-hp-ntfy)). Di `waifu chat` kalimatnya dicetak, kecuali
  selagi `waifu run` berjalan: pemberitahuannya diserahkan ke sana, supaya tetap diucapkan dan
  dikirim ke HP.
  Bulan berikutnya hitungannya mulai lagi dari nol. Pemberitahuan yang sudah diberikan
  dicatat di file yang sama, jadi restart tidak mengulanginya; kalau anggaran dinaikkan di
  tengah bulan, pemberitahuan yang sudah lewat tidak diulang.
- **Kurs rupiah:** `idr_per_usd` tidak diambil dari internet. Isi dengan kurs yang kamu
  pakai dan perbarui sendiri sesekali; tanpa kurs, dia menyebut biayanya dalam dolar saja.
- **Harga:** `prices` ditimpakan ke harga bawaan per model. Tulis keempat angkanya (input,
  output, cache_write, cache_read). Kalau `brain.claude.model` diganti ke model lain,
  tambahkan harga model itu di sini supaya tidak dihitung sebagai perkiraan dengan harga
  `default_model`. `default_model` sendiri harus punya harga (bawaan atau di `prices`):
  kalau tidak, `waifu run`, `waifu chat`, dan `waifu usage` berhenti dengan
  `Konfigurasi bermasalah: usage.default_model ... has no prices` dan `waifu check`
  menandainya `✗`, daripada diam-diam menghitung biayanya 0 dolar. Harga baru hanya berlaku
  untuk jawaban sesudahnya; yang sudah tercatat tidak dihitung ulang.

Jalankan ulang `waifu run` setelah mengubahnya. Contoh lengkapnya ada di
`config.example.yaml`.

### File data\usage.json

Berisi total token, jumlah permintaan, dan biaya per hari per model, serta pemberitahuan
anggaran yang sudah diberikan per bulan; tidak ada isi obrolan. Seperti file Cyrene lainnya,
file ini hanya ada di PC ini, tidak di-commit, ditulis secara atomik, dan file yang rusak
tidak pernah ditimpa: namanya diganti menjadi `usage.json.corrupt-<tanggal>-<jam>`, dia
mulai mencatat dari kosong, dan `waifu check` menunjukkannya dengan `!` selama file itu masih
ada. Untuk memulihkan, matikan Cyrene, perbaiki file `.corrupt-...` itu, hapus
`usage.json` yang baru, lalu ganti nama file yang sudah diperbaiki kembali menjadi
`usage.json` (catatan sejak file itu dipindah ikut hilang). Untuk mulai dari nol, hapus
filenya saat Cyrene mati.

Untuk berhenti mencatat: `usage.enabled: false`. Dia tidak lagi mencatat atau memberi tahu
anggaran dan tidak lagi bisa menjawab pertanyaan biaya; catatan lamanya tetap ada dan masih
bisa dilihat dengan `waifu usage`.

## Cyrene di HP

Selama `waifu run` berjalan di PC, Cyrene juga bisa dibuka dari HP Android Master, di rumah
atau di luar: avatarnya (model yang sama dengan maskot, dibingkai setengah badan), chat teks,
tombol mikrofon yang ditahan selama bicara, tombol cepat untuk lampu, AC, colokan, dan
rutinitas (lihat [Tombol cepat di HP](#tombol-cepat-di-hp)), dan dengan otak Claude, kamera HP
untuk menunjukkan sesuatu padanya (lihat [Kamera HP](#kamera-hp)). Dia menjawab dengan teks dan
dengan suara animenya dari speaker HP; mulut avatar ikut bergerak dan wajahnya menunjukkan
emosi jawaban. Ini Cyrene yang sama: ingatan, perangkat rumah, jadwal, game, sapaan pagi, dan
aturan konfirmasinya sama, jadi "matikan lampu kamar" dari HP benar-benar mematikan lampu, dan
"matikan komputer" tetap dijawab dengan pertanyaan dulu. Jawaban untuk HP tidak diucapkan di
speaker PC.

```text
 HP (Chrome) ──HTTPS──► Tailscale: sertifikat asli *.ts.net, hanya perangkat di akunmu
                          │ tailscale serve (sekali, tetap berlaku setelah restart)
                          ▼
                 server HP Cyrene di 127.0.0.1:9883: hanya HP yang sudah dipasangkan
                          │
                          ▼
                 Cyrene yang sama dengan di PC (otak, ingatan, perangkat)
```

- **Tidak ada port yang dibuka** ke internet atau ke Wi-Fi rumah. Server HP hanya
  mendengarkan di `127.0.0.1`; Tailscale yang meneruskan dari HP, terenkripsi, dan hanya
  perangkat yang login ke akun Tailscale-mu yang bisa mencapainya. Jangan pakai
  `tailscale funnel` untuk alamat PC ini: Funnel membukanya ke seluruh internet, dan server HP
  menolak setiap permintaan lewat Funnel. Di luar rumah cukup nyalakan aplikasi Tailscale di HP.
- **Hanya HP yang dipasangkan.** HP baru harus memasukkan kode 6 angka dari PC sekali.
  Tanpa itu halaman hanya menampilkan formulir pasangan, tanpa avatar atau jawaban.

### Kebutuhan

- [Tailscale](https://tailscale.com/download) (gratis untuk pemakaian pribadi) di PC Windows
  dan di HP Android, login dengan akun yang sama.
- Chrome di Android.
- Model avatar di bagian `avatar` di `config.yaml` (`avatar.engine`, `avatar.model`, dan untuk
  Live2D `avatar.live2d_core`; lihat [Avatar 3D](#avatar-3d-つくよみちゃん) atau
  [Avatar Live2D](#avatar-live2d--interaksi)). HP memakainya juga kalau `avatar.enabled: false`
  (maskot di PC mati, tapi halaman HP tetap butuh modelnya); tanpa `avatar.model` halaman HP
  hanya menampilkan chat.

### 1. Pasang Tailscale di PC dan HP (sekali)

1. **PC:** unduh Tailscale untuk Windows dari <https://tailscale.com/download>, pasang, lalu
   login dari ikon Tailscale di system tray.
2. **HP:** pasang aplikasi Tailscale dari Play Store, login dengan akun yang sama, dan
   nyalakan sambungannya (Android menanyakan izin VPN; izinkan).
3. **Admin console Tailscale**, halaman **DNS** (<https://login.tailscale.com/admin/dns>):
   nyalakan **MagicDNS** kalau belum, lalu di bagian **HTTPS Certificates** pilih
   **Enable HTTPS**. Tailscale memperingatkan bahwa nama mesin dan nama tailnet-mu akan
   tercatat di buku besar publik (Certificate Transparency); itu syarat sertifikat HTTPS
   asli. Jadi jangan beri PC nama yang berisi data pribadi.

### 2. `waifu phone setup` dan `tailscale serve` (sekali)

```powershell
.venv\Scripts\waifu phone setup
```

Perintah ini mencari Tailscale (di PATH, atau di `C:\Program Files\Tailscale\tailscale.exe`),
membaca nama PC ini di Tailscale dari `tailscale status --json`, lalu menampilkan tiga hal:

```text
✓ Tailscale: C:\Program Files\Tailscale\tailscale.exe
✓ Nama PC ini di Tailscale: pc-lynx.tail1234.ts.net

1. Jalankan sekali di Command Prompt. ...
   tailscale serve --bg --https=443 http://127.0.0.1:9883
2. Isi bagian phone di config.yaml:
   phone:
     enabled: true
     host: pc-lynx.tail1234.ts.net
3. Jalankan ulang 'waifu run', lalu 'waifu phone pair', dan buka alamat ini di Chrome HP:
   https://pc-lynx.tail1234.ts.net/
```

`waifu phone setup` hanya membaca status Tailscale; dia tidak pernah mengubah pengaturannya.
Yang membagikan Cyrene adalah perintah `tailscale serve` itu, yang kamu jalankan sendiri
**sekali** di Command Prompt (kalau Tailscale tidak ada di PATH, perintah yang ditampilkan
memakai path lengkapnya). Dengan `--bg` pengaturan itu tetap berlaku setelah PC atau
Tailscale dinyalakan ulang. Untuk berhenti membagikan: `tailscale serve --https=443 off`.
Port `9883` adalah `phone.port`; kalau kamu menggantinya, jalankan lagi `waifu phone setup`
untuk perintah dengan port yang baru (halaman HP hanya jalan di HTTPS port 443).

### 3. Nyalakan di config.yaml

Tulis bagian yang ditampilkan `waifu phone setup` (contoh lengkapnya, dengan komentar, ada di
`config.example.yaml`):

```yaml
phone:
  enabled: true
  host: pc-lynx.tail1234.ts.net   # nama PC ini di Tailscale, persis seperti dari waifu phone setup
```

`host` wajib: server HP hanya melayani nama itu (serta `127.0.0.1` dan `localhost`), jadi
tanpa `host` HP ditolak dengan 403. Jalankan ulang `waifu run`; saat siap dia menampilkan
`Cyrene di HP: https://pc-lynx.tail1234.ts.net/`. Server HP hanya hidup selama `waifu run`
(tidak di `waifu chat`).

Cek semuanya dengan `waifu check`:

```text
✓ Cyrene di HP: server di 127.0.0.1:9883 selama 'waifu run'
✓ Tailscale: C:\Program Files\Tailscale\tailscale.exe
✓ Tailscale tersambung; nama PC ini pc-lynx.tail1234.ts.net
✓ tailscale serve membagikan https://pc-lynx.tail1234.ts.net/ ke 127.0.0.1:9883
✓ phone.host: pc-lynx.tail1234.ts.net
✓ Kamera HP: aktif
✓ HP terpasang: 1 (lihat 'waifu phone devices')
```

Baris `✗` menyebut apa yang kurang: Tailscale belum terpasang atau belum login, `tailscale
serve` belum dijalankan (beserta perintahnya), Tailscale Funnel menyala untuk alamat PC ini
(beserta perintah untuk mematikannya), atau `phone.host` kosong atau bukan nama PC ini. Baris
`!` tentang `avatar.model` berarti halaman HP hanya menampilkan chat, tanpa avatar. Baris
`! Kamera HP` menyebut kenapa tombol kamera tidak ada (lihat [Kamera HP](#kamera-hp)).

### 4. Pasangkan HP dan pasang di layar utama

1. Selama `waifu run` berjalan, jalankan di PC:

   ```powershell
   .venv\Scripts\waifu phone pair
   ```

   Dia menampilkan kode 6 angka, misalnya `Kode pasangan HP: 482915`, dan sampai jam berapa
   kode itu berlaku (10 menit). Satu kode untuk satu HP; kode baru menggantikan yang lama, dan
   5 kali salah membuangnya (buat kode baru).
2. Di HP, buka alamat `https://<nama-pc>.<tailnet>.ts.net/` dari `waifu phone setup` di
   Chrome, masukkan kodenya, lalu ketuk **Pasangkan**. HP itu diingat lewat cookie selama
   setahun; kodenya sendiri tidak pernah ditulis ke log atau disimpan apa adanya.
3. Supaya terbuka seperti aplikasi: menu Chrome (⋮) → **Tambahkan ke layar utama**
   (atau **Instal aplikasi**). Ikon Cyrene muncul di layar utama dan membuka halaman tanpa
   bilah alamat.

Pembukaan pertama mengunduh model avatar dari PC: model Live2D Cyrene dengan tekstur 4096 px
sekitar 40 MB, ditambah Cubism Core dan pustaka renderernya. Chrome menyimpannya, jadi
pembukaan berikutnya hanya menanyakan ke PC apakah filenya berubah (beberapa kilobyte). Kalau
kuota data terbatas, buka pertama kali lewat Wi-Fi; setelah model di PC diganti, HP mengunduhnya
sekali lagi.

### Memakai di HP

- **Chat:** ketik di kotak pesan lalu kirim. Jawabannya muncul sebagai gelembung dan
  diucapkan dari speaker HP.
- **Bicara:** tahan tombol mikrofon selama bicara, lepaskan untuk mengirim. Tidak perlu
  memanggil namanya dan tidak ada verifikasi suara (HP-nya sudah dipasangkan, dan voiceprint
  dari mic PC tidak cocok untuk mic HP). Yang dia dengar tampil dulu, lalu jawabannya. Paling
  lama 60 detik per rekaman; pesan teks paling panjang 2000 karakter.
- **Suara baru keluar setelah sentuhan pertama.** Chrome tidak mengizinkan halaman
  memutar suara sebelum Master menyentuhnya, jadi ketuk layar sekali setelah halaman dibuka.
- Satu pertanyaan sekaligus: selagi dia masih menjawab, pertanyaan berikutnya dari HP yang sama
  ditolak dengan "Sebentar ya, Master, aku masih menjawab yang tadi." Kalau di PC Master
  sedang bicara dengannya, jawaban HP menunggu sampai giliran itu selesai dipikirkan.
- Selagi Master mengobrol lewat HP, dia tidak memulai obrolan baru di PC: sapaan pagi, laporan
  mingguan, dan pertanyaan jurnal malam menunggu sampai sekitar 10 menit setelah pesan HP
  terakhir (`assistant.session_idle_minutes`), supaya obrolan di HP tidak terhapus.
- Konfirmasi tetap berlaku: "matikan komputer" dari HP ditanyakan dulu, dan "iya" berikutnya
  (dari HP atau dari mic PC) yang menjalankannya. Lihat layar ("lihat layarku") juga bisa,
  yang dilihat adalah layar PC. Untuk menunjukkan sesuatu di depan HP, pakai
  [kamera HP](#kamera-hp): selama jendela kamera terbuka, hanya kata yang menyebut layarnya
  (`layar`, `layarku`, `screen`, `screenshot`, `monitor`) yang membuatnya melihat layar PC;
  "lihat ini" atau "coba liat deh" berarti kamera HP.

### Kamera HP

Dengan otak Claude, Cyrene bisa melihat lewat kamera HP. Ketuk tombol **kamera** di sebelah
tombol kirim: jendela kamera terbuka di bagian bawah halaman dengan kamera belakang (tombol
**putar** menggantinya ke kamera depan, **×** menutupnya). Selama jendela itu terbuka, setiap
pesan yang diketik, atau diucapkan lalu dilepas tombol mikrofonnya, dikirim bersama **satu
foto** yang diambil saat itu juga, dan Cyrene menjawab tentang yang terlihat. Gelembung pesan
Master menampilkan gambar kecil dari foto yang persis dia lihat.

```text
Master : "ini apa?"                             (kamera belakang ke arah kucing)
Cyrene : "Itu kucing oranye, Master! Lagi tidur di sofa, lucu banget."
Master : "lihat ini, bagus nggak bajuku?"       (kamera depan)
Master : "tanaman ini kenapa daunnya kuning?"   (kamera ke arah tanaman)
```

- **Hanya saat jendela kamera terbuka.** Tanpa jendela kamera tidak ada yang difoto. Cyrene
  tidak bisa membuka kamera sendiri, tidak merekam video, dan tidak mengawasi terus-menerus:
  satu foto untuk setiap pesan yang Master kirim, tidak lebih.
- **Kamera, bukan layar PC.** Pesan yang dikirim selama jendela kamera terbuka ditandai
  "lewat kamera HP", juga kalau fotonya gagal diambil. Di pesan seperti itu "lihat ini" atau
  "coba liat deh" tidak pernah membuatnya mengambil tangkapan layar PC (kode Cyrene sendiri
  yang menolaknya); hanya kata yang menyebut layarnya, seperti "lihat layarku", yang masih
  membuatnya melihat layar PC.
- **Kamera mati sendiri** saat jendela kamera ditutup, saat halaman disembunyikan atau
  ditinggalkan (pindah aplikasi, layar dikunci, tab ditutup), dan **2 menit** setelah jendela
  dibuka, setelah foto terakhir, atau setelah tombol mikrofon terakhir ditekan, mana yang
  paling akhir (jadi kamera tidak mati selagi tombol mikrofon ditahan). Selama kamera menyala,
  Android (12 ke atas) menampilkan tanda kamera di pojok kanan atas bilah status.
- **Ukuran foto:** halaman mengecilkan foto sampai sisi terpanjangnya paling besar
  `vision.max_side` (bawaan 1280 piksel; perbandingannya tetap, tidak pernah diperbesar), lalu
  mengirimnya sebagai JPEG. Foto di atas 3,5 MB dikompres lebih kuat; kalau masih terlalu
  besar, pesannya terkirim tanpa foto dan halaman memberi tahu.
- **Hanya dengan Claude dan `vision.enabled: true`.** Dengan Ollama tombol kamera tidak muncul
  (qwen3:8b tidak bisa melihat gambar) dan server HP menolak foto. `waifu check` menampilkan
  `✓ Kamera HP: aktif`, `! Kamera HP: tidak tersedia dengan Ollama`,
  `! Kamera HP mati (vision.enabled: false)`, atau `! Kamera HP mati (phone.camera: false)`.

**Izin kamera di Chrome.** Saat tombol kamera pertama kali diketuk, Chrome menanyakan izin
kamera untuk alamat Cyrene; pilih izinkan (kalau Android juga menanyakan izin kamera untuk
Chrome, izinkan juga). Kalau terlanjur ditolak, halaman menampilkan "Izin kamera ditolak":
buka menu Chrome ⋮ → **Setelan** → **Setelan situs** → **Kamera**, ketuk alamat `...ts.net`
di bagian yang diblokir, pilih **Izinkan**, lalu muat ulang halaman. Ikon Cyrene di layar
utama memakai izin yang sama dengan Chrome. Cek juga Setelan Android → Aplikasi → Chrome →
Izin → Kamera. "Kamera sedang dipakai aplikasi lain" berarti aplikasi lain (misalnya video
call) masih memegang kamera: tutup dulu aplikasi itu.

**Privasi foto.**

- Foto dikirim ke API Anthropic bersama pesan itu, seperti tangkapan layar, dan tetap ada di
  riwayat sesi itu sampai sesinya berakhir (10 menit tanpa percakapan atau 40 giliran), jadi
  dia masih bisa menjawab pertanyaan lanjutan tentang foto yang sama. Begitu gambar di riwayat
  (foto dan tangkapan layar) mencapai 10 MB, sesi baru dimulai sendiri (lihat
  [Privasi layar](#privasi-layar)). Setiap foto menambah biaya API.
- Apa pun yang tertangkap kamera ikut terkirim: orang lain, dokumen, layar. Arahkan kameranya
  dengan sadar.
- Foto **tidak pernah disimpan ke disk dan tidak ditulis ke log** di PC, juga dengan `-v` dan
  `--log-file`; log hanya mencatat ukuran dan dimensinya. Di HP, halaman hanya memegang gambar
  kecilnya di memori selama gelembungnya masih tampil (tidak di penyimpanan Chrome); begitu
  gelembung itu keluar dari daftar chat atau halaman ditutup, gambarnya hilang.
- Cyrene diberi tahu bahwa foto itu dari kamera HP Master dan itulah yang Master tunjukkan,
  dan diminta tidak menyimpan isinya ke ingatannya kecuali Master memintanya.

**Mematikannya.** Tambahkan `camera: false` ke bagian `phone` di `config.yaml`, jalankan ulang
`waifu run`, lalu muat ulang halaman di HP: tombol kamera hilang dan server HP menolak foto.
`vision.enabled: false` mematikan kamera HP sekaligus fitur [melihat layar](#melihat-layar).

```yaml
phone:
  enabled: true
  host: pc-lynx.tail1234.ts.net
  camera: false
```

### Tombol cepat di HP

Untuk hal sekali ketuk tidak perlu mengetik ke Cyrene: tombol **lampu** di kiri kotak pesan
membuka panel **Tombol cepat** di tempat jendela kamera (ketuk lagi untuk menutupnya; membuka
kamera juga menutupnya). Selagi Master mengetik pesan, tombol lampu menyingkir supaya kotak
pesannya lebih lebar. Tombolnya
dijalankan langsung di PC lewat daftar perangkat, seperti `waifu device` dan
`waifu routine run`, tanpa otak Cyrene: lebih cepat, tanpa biaya API, dan dia tidak berbicara.
Hasilnya muncul 6 detik di atas panel (tombolnya tidak bergeser), misalnya "Lampu kamar dinyalakan." atau "Mode
tidur: 3 dari 3 langkah berhasil. Dijadwalkan jam 01.00 dini hari nanti."; kalau perangkatnya
gagal, alasannya yang muncul ("Lampu kamar tidak bisa dihubungi ...").

```text
Lampu kamar     [Nyala] [Mati] [Nyala/mati] [10%] [30%] [60%] [100%]
AC Samsung      [Nyala] [Mati] [18°C] [20°C] [22°C] [24°C] [26°C] [28°C]
Proyektor       [Tombol power]
Rutinitas       [Mode tidur]
```

- **Yang muncul:** setiap lampu, colokan, dan perangkat IR, dengan tombol yang memang ada
  padanya: nyala, mati, nyala/mati, dan tombol power remote; untuk lampu juga kecerahan 10, 30,
  60, dan 100%; untuk perangkat IR (AC) suhu 18, 20, 22, 24, 26, dan 28 °C yang sudah dipelajari
  dengan `waifu learn-ir` (kode yang baru dipelajari muncul setelah halaman dimuat ulang).
  Lalu semua rutinitas di grup **Rutinitas**, dengan `description`-nya atau namanya. Tidak ada
  tombol suhu +/-: Cyrene tidak tahu suhu yang diubah lewat remote, jadi tombolnya menyebut suhu
  yang pasti. Dengan `waifu run --dry-run` tombolnya menjalankan perangkat simulasi.
- **PC tidak pernah jadi tombol.** Tidak ada tombol untuk mematikan, me-restart, menidurkan,
  atau menyalakan PC, dan sama sekali tidak ada tombol untuk colokan yang mengalirkan listrik ke
  PC (`powers_pc: true`), juga bukan "nyala". Itu tetap lewat suara atau chat, dengan pertanyaan
  konfirmasi dulu. Server HP juga menolaknya kalau diminta langsung, dan `config.yaml` ditolak
  kalau `quick_actions` menyebutnya.
- Musik dan volume PC (type `media`) dan aplikasi (type `apps`) juga tidak jadi tombol: itu
  bukan sakelar nyala/mati, dan membuka aplikasi mengambil fokus dari game.
- **Satu per satu:** selagi satu tombol berjalan (lampu Tuya bisa perlu beberapa detik saat
  pertama kali dihubungi), tombol itu tampil sibuk dan tombol lain menunggu; chat dan tombol
  mikrofon tetap bisa dipakai. Tombol yang ditekan lagi dari tab lain atau sesudah halaman
  dimuat ulang selagi yang tadi masih berjalan menjawab "Perintah tadi masih jalan." Paling
  banyak 10 tombol per menit per HP; lebih dari itu muncul "Tombolnya terlalu sering ditekan."
- **Cyrene tidak tahu tombol yang ditekan.** Kalau tadi dia bilang lampu menyala lalu Master
  mematikannya lewat tombol, tanya lagi ("lampu kamar nyala nggak?") supaya dia membaca
  keadaannya sekarang.
- Log `waifu run` mencatat HP dan tombolnya
  (`Phone device d1 ran quick action dev.lampu_kamar.on: ok`), tidak pernah token HP.
- **Alarm:** selagi [alarm](#alarm-bangun) berbunyi, grup **Alarm** muncul paling atas dengan
  **Matikan alarm** dan **Tunda 5 menit**. Daftar tombolnya dibaca lagi setiap kali panel dibuka
  dan setiap kali halamannya tampil lagi (misalnya dari notifikasi alarm), dan panelnya terbuka
  sendiri kalau ada tombol alarm. Selama grup **Alarm** tampil, daftarnya juga dibaca lagi
  setiap 15 detik dan langsung sesudah tombol alarm ditekan, jadi grupnya hilang begitu alarmnya
  berhenti, entah lewat tombol, suara, atau karena waktunya habis.

**Memilih dan mengurutkan tombol.** Tanpa pengaturan, semua perangkat dan rutinitas di atas
muncul dengan urutan di `config.yaml`. Untuk menampilkan sebagian saja, dengan urutanmu sendiri:

```yaml
phone:
  enabled: true
  host: pc-lynx.tail1234.ts.net
  quick_actions:
    devices: [lampu_kamar, ac]   # hanya ini, dengan urutan ini; [] = tanpa perangkat
    routines: [tidur]            # tidak ditulis = semua rutinitas; [] = tanpa rutinitas
```

`config.yaml` ditolak dengan pesan yang jelas kalau sebuah nama tidak ada, disebut dua kali,
atau menyebut PC, colokan PC, musik, atau aplikasi. Jalankan ulang `waifu run`, lalu muat ulang
halaman di HP. Tanpa tombol sama sekali (misalnya `devices: []` dan `routines: []`), tombol
lampu di HP tidak muncul.

Tombol yang sama bisa dibuka di browser PC dengan `waifu tombol`: lihat
[Tombol cepat di PC](#tombol-cepat-di-pc).

### Mengatur HP yang dipasangkan

```powershell
.venv\Scripts\waifu phone devices        # daftar HP: id, nama, kapan dipasangkan dan terakhir dipakai
.venv\Scripts\waifu phone forget d1      # lupakan satu HP; berlaku seketika, juga selagi waifu run berjalan
```

Waktu terakhir dipakai diperbarui paling sering sejam sekali. HP yang hilang, dijual, atau
dipinjamkan: lupakan dengan `waifu phone forget <id>`, lalu kalau perlu keluarkan juga
perangkat itu dari akun Tailscale di admin console (halaman Machines). HP yang dilupakan
melihat formulir pasangan lagi dan harus memakai kode baru. Daftar HP ada di
`data\phone.json`: hanya hash token dan kode, nama, dan waktunya; file yang rusak dipindah ke
`phone.json.corrupt-<tanggal>-<jam>` dan semua HP harus dipasangkan ulang. File kosong
`phone.json.lock` di sebelahnya membuat `waifu phone pair`/`forget` dan `waifu run` bergantian
mengubah daftar itu, jadi HP yang dilupakan tidak pernah kembali.

### Privasi Cyrene di HP

- File model avatar dikirim ke HP dan digambar di HP: untuk Live2D seluruh folder modelnya
  (model3.json, moc3, physics, dan teksturnya) beserta Cubism Core milikmu, untuk VRM file
  `.vrm`-nya. Semuanya hanya dikirim ke HP yang sudah dipasangkan, lewat sambungan Tailscale
  yang terenkripsi, dan tersimpan di cache Chrome HP itu (hapus dengan menghapus data situs di
  Chrome). Periksa apakah lisensi model dan Core mengizinkan itu.
- Model suara anime (RVC) tetap di PC; HP hanya menerima teks jawaban dan klip suara jawaban
  yang sudah jadi.
- Rekaman suara dari HP dikirim ke PC dan diubah jadi teks di PC (Whisper, lokal); rekaman itu
  tidak disimpan.
- Teks dari HP diperlakukan seperti kalimat lewat mic: dengan otak Claude dikirim ke API
  Anthropic seperti biasa, dan tercatat di log (`Phone message: ...`, `Heard on the phone ...`).
- Foto dari kamera HP hanya diambil selama jendela kamera terbuka, dikirim ke API Anthropic
  bersama pesannya, dan tidak pernah disimpan atau ditulis ke log; lihat
  [Kamera HP](#kamera-hp).
- [Tombol cepat](#tombol-cepat-di-hp) dijalankan di PC tanpa otak Cyrene: tidak ada yang
  dikirim ke API Anthropic, dan log hanya mencatat HP, tombol, dan berhasil atau gagal.
- Tailscale melihat bahwa HP dan PC tersambung, tapi isi sambungannya terenkripsi ujung ke
  ujung. Nama PC dan nama tailnet tercatat di log Certificate Transparency publik (syarat
  sertifikat HTTPS).
- Untuk mematikan: `phone.enabled: false` dan jalankan ulang `waifu run`; untuk berhenti
  membagikan sama sekali: `tailscale serve --https=443 off`.

### Pemecahan masalah Cyrene di HP

**Halaman tidak terbuka di HP, atau "Tidak bisa terhubung ke PC".** Pastikan Tailscale di HP
menyala (ikon kunci VPN di bilah status), `waifu run` berjalan di PC, dan `waifu check`
menampilkan `✓` untuk Tailscale dan `tailscale serve`. Buka alamat persis
`https://<nama-pc>.<tailnet>.ts.net/` (dengan `https`). Aplikasi yang sudah dipasang di layar
utama tetap terbuka saat `waifu run` mati (Tailscale lalu menjawab untuk PC dengan 502), dan
menampilkan pesan itu sampai Cyrene berjalan lagi.

**Formulir pasangan terus muncul, atau "HP ini belum dipasangkan" (401).** HP belum
dipasangkan, sudah dilupakan (`waifu phone forget`), atau cookie Chrome-nya dihapus. Buat kode
baru dengan `waifu phone pair` dan masukkan lagi. "Kodenya salah atau sudah kedaluwarsa"
berarti kode salah ketik atau lewat 10 menit; "Terlalu banyak percobaan" berarti kode sudah
dibuang setelah 5 kali salah.

**`{"error":"Server HP Cyrene hanya melayani alamat Tailscale PC ini, 127.0.0.1, atau
localhost."}` atau "PC menolak permintaan dari alamat ini" (403).** `phone.host` kosong atau
tidak sama dengan alamat yang dibuka di HP. Halamannya lalu tidak termuat sama sekali, jadi
Chrome hanya menampilkan teks JSON itu (pesan kedua muncul di halaman yang sudah terbuka).
Isi `phone.host` persis seperti dari `waifu phone setup`, lalu jalankan ulang `waifu run`.
`waifu check` menunjukkan kalau `phone.host` bukan nama PC ini.

**`{"error":"Server HP Cyrene hanya melayani jaringan Tailscale-mu, bukan internet lewat
Tailscale Funnel. ..."}` (403).** Tailscale Funnel menyala untuk alamat PC ini, jadi permintaan
datang dari internet. Matikan dengan `tailscale funnel --https=443 off`, lalu jalankan lagi
perintah `tailscale serve` dari `waifu phone setup`; `waifu check` menampilkan perintahnya.

**Mikrofon tidak bisa dipakai.** Saat pertama menahan tombol mikrofon, Chrome meminta izin;
pilih izinkan. Kalau terlanjur ditolak: ketuk ikon di kiri alamat (atau Setelan situs di
menu Chrome) → **Mikrofon** → **Izinkan**, lalu muat ulang halaman. Cek juga Setelan Android →
Aplikasi → Chrome → Izin → Mikrofon.

**Tombol kamera tidak ada, atau "Kamera HP sudah dimatikan di PC".** Lihat baris `Kamera HP`
di `waifu check`: kamera HP hanya ada dengan `brain.backend: claude`, `vision.enabled: true`,
dan tanpa `phone.camera: false`. Setelah mengubah `config.yaml`, jalankan ulang `waifu run`
lalu muat ulang halaman di HP. Halaman yang sudah terbuka sebelum kameranya dimatikan masih
menampilkan tombol kamera; pesan pertama yang dikirim lewat jendela kamera ditolak PC, dan
halaman lalu menampilkan "Kamera HP sudah dimatikan di PC, jadi pesan tadi tidak terkirim.
Kirim lagi tanpa kamera.", menutup jendela kamera, dan menyembunyikan tombolnya. Pesan yang
diketik kembali ke kotak pesan; pesan suara diucapkan lagi.

**Kamera tidak bisa dibuka.** "Izin kamera ditolak": izinkan seperti di
[Kamera HP](#kamera-hp). "Kamera sedang dipakai aplikasi lain": tutup aplikasi yang memakai
kamera (video call, aplikasi kamera), lalu ketuk tombol kamera lagi.

**"Fotonya tidak bisa diambil" atau "Fotonya terlalu besar untuk dikirim".** Pesannya tetap
terkirim, hanya tanpa foto (tetap ditandai lewat kamera HP, jadi "lihat ini" tidak membuatnya
melihat layar PC). Cyrene hanya menerima kata-katanya dan baru tahu fotonya tidak sampai kalau
dia mencoba melihat layar, jadi kirim lagi pesannya setelah jendela kamera menampilkan gambar.
Kalau foto sering terlalu besar, kecilkan `vision.max_side`.

**Jawaban hanya teks, tanpa suara.** Ketuk layar sekali setelah halaman dibuka (aturan
autoplay Chrome), dan pastikan volume media HP tidak 0. Kalau suaranya gagal dibuat di PC
(misalnya edge-tts tanpa internet dan belum ada [suara cadangan](#suara-cadangan-offline)),
jawaban tetap tampil sebagai teks dan log PC menyebut `Could not synthesise the phone reply`.

**`tailscale serve` gagal atau ditolak.** Pastikan HTTPS Certificates sudah dinyalakan di
admin console (langkah 1). Kalau ditolak karena hak akses, jalankan Command Prompt sebagai
administrator lalu ulangi perintahnya.

**`Cyrene di HP tidak tersedia: Port 127.0.0.1:9883 sudah dipakai`.** Ada program lain (atau
Cyrene lain) di port itu. Tutup program itu, atau ganti `phone.port`, jalankan lagi
`waifu phone setup`, dan jalankan perintah `tailscale serve` yang baru (matikan yang lama dengan
`tailscale serve --https=443 off`).

## Kesehatan PC

Cyrene bisa membaca kondisi PC ini saat ditanya: beban CPU, RAM yang terpakai, sisa ruang tiap
disk (C:, D:, ...), suhu, beban, VRAM, dan daya GPU NVIDIA, serta sudah berapa lama sejak Windows
terakhir di-restart. Selama `waifu run` dia juga bilang sendiri kalau sebuah disk hampir penuh atau
GPU terus panas. Fitur ini sudah menyala dari awal (`pc_health.enabled: true`), dengan otak Ollama
maupun Claude, tanpa paket tambahan: CPU, RAM, disk, dan waktu sejak restart dibaca langsung dari
Windows, dan GPU dari `nvidia-smi`, yang terpasang bersama driver NVIDIA.

```text
Master : "Cyrene, PC-ku gimana?"
Cyrene : "Aman, Master. CPU-nya 23 persen, RAM kepakai 12 dari 32 GB, GPU 54 derajat."
Master : "GPU-ku panas nggak?"                 (tanpa memanggil nama, masih dalam jendela lanjutan)
Master : "Cyrene, RAM kepake berapa?"
Master : "Cyrene, disk C sisa berapa?"
Master : "Cyrene, sudah berapa lama sejak PC-ku terakhir restart?"
```

Setiap kali ditanya dia membaca PC saat itu juga, tidak menebak, dan menjawab dalam satu atau
dua kalimat dengan angka yang dibulatkan. Gigabyte-nya dihitung seperti Windows Explorer
(1 GB = 1024 × 1024 × 1024 byte), jadi sisa disk sama dengan yang terlihat di Explorer. Beban CPU
adalah beban beberapa detik terakhir: kalau PC terakhir dibaca paling lama 10 detik sebelumnya
(misalnya oleh pemberitahuan selama `waifu run`), rata-rata sejak itu; kalau tidak, dia mengukurnya
setengah detik saat itu juga. Waktu sejak restart dihitung sejak Windows terakhir di-restart, dengan
waktu sleep ikut terhitung: sleep dan shutdown dengan Fast Startup (bawaan Windows 11) tidak
mengulangnya, jadi bisa jauh lebih lama dari sejak PC dinyalakan pagi ini.

### Pemberitahuan dan pengaturannya

- **Disk hampir penuh.** Sisa sebuah disk di bawah `disk_warn_gb` (bawaan 10 GB): "Master, disk
  C: tinggal 8,4 GB. Mungkin perlu dibersihkan sedikit." Sisanya dibulatkan ke bawah, jadi disk
  yang sedikit di bawah batas tidak terdengar seperti batasnya (9,96 GB disebut 9,9). Sekali per
  disk per hari (tanggal PC): kalau sisanya naik lagi lalu turun lagi di hari yang sama, dia diam.
  Huruf drive dari `subst` (folder yang dipasang sebagai drive) tidak dihitung sebagai disk sendiri.
- **GPU terus panas.** GPU di `gpu_warn_c` (bawaan 85°C) atau lebih di setiap pembacaan selama
  paling sedikit `gpu_warn_minutes` (bawaan 3 menit): "Master, GPU-nya sudah 88°C beberapa menit.
  Istirahat sebentar atau cek pendinginnya, ya." Paling sering sekali per jam. Satu pembacaan di
  bawah batas, atau tanpa angka suhu, membuat hitungannya mulai dari awal. Kalau kalimatnya
  ditahan [Mode game](#mode-game) sampai sejam, kalimat itu dilewati, jadi tidak menumpuk selama
  main lama; notifikasi HP-nya, kalau menyala, sudah terkirim. Blok fokus tanpa main game
  menahannya sampai istirahat seperti biasa.
- PC dibaca setiap `poll_seconds` (bawaan 60 detik), mulai begitu dia siap mendengarkan.
- Seperti pengingat, kalimatnya diucapkan di sela obrolan, tidak pernah memotong Master atau
  jawabannya, dengan wajah sedih, dan dikirim juga ke HP kalau notifikasi menyala (lihat
  [Notifikasi ke HP](#3-notifikasi-ke-hp-ntfy)).
- Yang sudah diberitahukan hanya diingat selama `waifu run` berjalan (tidak ada yang ditulis ke
  disk), jadi setelah restart satu pemberitahuan bisa terulang sekali.
- Hanya selama `waifu run`. `waifu chat` bisa ditanya, tapi tidak bilang sendiri.

```yaml
pc_health:
  enabled: true        # false: tanpa tool dan tanpa pemberitahuan (`waifu pc` tetap jalan)
  notices: true        # false: hanya menjawab kalau ditanya, tidak pernah bilang sendiri
  poll_seconds: 60     # baca PC tiap N detik selama `waifu run` (15-3600)
  disk_warn_gb: 10     # batas sisa disk dalam GB (1-2000)
  gpu_warn_c: 85       # batas suhu GPU dalam °C (60-100)
  gpu_warn_minutes: 3  # berapa menit berturut-turut GPU di atas batas sebelum dia bilang (1-60)
```

Bagian ini tidak perlu ditulis untuk memakai bawaannya; contohnya ada di `config.example.yaml`.
Jalankan ulang `waifu run` setelah mengubahnya.

### Melihat angkanya: `waifu pc`

`waifu pc` menampilkan data yang sama dengan yang dibaca Cyrene, tanpa otak, satu baris per
bagian:

```text
CPU: 23%
RAM: 12,3 dari 31,9 GB terpakai (39%)
Disk C: tinggal 120,5 dari 465,1 GB (26% kosong)
Disk D: tinggal 8,4 dari 931,5 GB (1% kosong)
GPU: NVIDIA GeForce RTX 5060 Ti, 54°C, beban 12%, VRAM 3,2 dari 15,9 GB, daya 45,1 W
Sejak restart: 5 jam 12 menit
```

Bagian yang tidak bisa dibaca ditulis `tidak terbaca`, dan angka yang tidak dilaporkan
`nvidia-smi` dilewati. Tanpa GPU NVIDIA atau tanpa `nvidia-smi`, barisnya
`GPU: tidak terbaca (bukan NVIDIA atau nvidia-smi tidak ada)`. Perintah ini butuh sekitar setengah
detik untuk mengukur CPU, dan jalan walau `pc_health.enabled: false`, juga selagi `waifu run`
berjalan.

`waifu check` menampilkan `✓ Kesehatan PC: aktif, GPU NVIDIA GeForce RTX 5060 Ti`,
`! Kesehatan PC: aktif, GPU tidak terbaca`, atau `! Kesehatan PC mati`; untuk itu `nvidia-smi`
ditanya sekali.

### Yang tidak bisa dia baca

- **Suhu CPU.** Windows tidak menyediakannya tanpa driver kernel (seperti milik
  LibreHardwareMonitor), jadi kalau ditanya dia bilang tidak bisa membacanya. Putaran kipas juga
  tidak.
- **GPU selain NVIDIA** (AMD, Intel): bagian GPU tidak ada. Dengan beberapa GPU NVIDIA, hanya
  yang pertama.
- **Flashdisk, kartu memori, drive jaringan, dan CD/DVD** tidak dihitung, dan disk BitLocker yang
  masih terkunci dilewati.
- **Program yang sedang berjalan** tidak pernah dibaca untuk kesehatan PC (namanya bisa ikut
  terkirim ke otaknya), jadi dia tidak tahu program mana yang berat. [Jam main](#jam-main) hanya
  mencocokkan nama tiga game dan melupakan sisanya. Dia boleh menyarankan menutup program berat atau
  membersihkan disk, tapi tidak pernah menutup program atau menghapus file sendiri, dan tidak
  pernah menyarankan membeli perangkat keras.

### Privasi kesehatan PC

Yang dibaca hanya angka, huruf drive, dan nama model GPU: tidak ada nama program, file, atau user
Windows. Tidak ada yang disimpan ke disk. Dengan otak Claude, angka itu ikut terkirim ke API
Anthropic sebagai hasil tool setiap kali Master bertanya (dengan Ollama, semuanya lokal), dan teks
pemberitahuannya ke `ntfy.sh` kalau notifikasi HP menyala.

Untuk tetap bisa bertanya tapi tanpa pemberitahuan, tulis `notices: false` di bagian
`pc_health`; untuk mematikan semuanya, `enabled: false`.

## Jam main

Selama `waifu run`, Cyrene mencatat berapa lama Honkai: Star Rail, Genshin Impact, dan Zenless
Zone Zero terbuka di PC ini, lalu menjawab kalau Master bertanya. Fitur ini sudah menyala dari
awal (`play_time.enabled: true`), dengan otak Ollama maupun Claude, tanpa paket tambahan.

```text
Master : "Cyrene, hari ini aku main berapa lama?"
Cyrene : "Hari ini HSR dua jam empat puluh menit dalam tiga sesi, Genshin setengah jam, Master."
Master : "Minggu ini Genshin berapa jam?"          (tanpa memanggil nama, masih dalam jendela lanjutan)
Master : "Cyrene, kemarin main ZZZ berapa lama?"
Master : "Cyrene, bulan ini total main game berapa jam?"
```

Dia hanya menyebutnya kalau ditanya: tidak di sapaan pagi atau obrolan yang dia mulai sendiri,
dan tidak pernah menceramahi. [Laporan game mingguan](#laporan-game-mingguan) ikut menyebut jam
main minggu itu.

### Yang dihitung

- **Game dikenali dari nama programnya** di daftar proses Windows, yang dibaca setiap
  `poll_seconds` (bawaan 60 detik), sepanjang hari: `StarRail.exe` (HSR), `GenshinImpact.exe`
  atau `YuanShen.exe` (Genshin versi Tiongkok), dan `ZenlessZoneZero.exe` (ZZZ). Launcher
  HoYoPlay (`HYP.exe`) dan `UnityCrashHandler64.exe` tidak dihitung, jadi launcher yang terbuka
  seharian bukan main. Nama HSR sudah terlihat langsung di PC ini; nama Genshin dan ZZZ diambil
  dari folder datanya dan belum. Kalau `waifu playtime` tidak mencatat game yang jelas terbuka,
  buka Task Manager → Details, lihat nama programnya, dan tulis di `play_time.games`.
- **Waktu game terbuka, bukan waktu benar-benar main.** Diminimalkan, ditinggal, atau di belakang
  jendela lain tetap terhitung.
- **Hanya selama `waifu run` menyala.** Main saat Cyrene mati tidak tercatat, dan `waifu chat`
  tidak mencatat.
- **Satu sesi** dimulai di pembacaan pertama yang melihat game, dan selesai di pembacaan
  terakhir yang melihatnya, setelah dua pembacaan berturut-turut tidak menemukannya (satu kali
  hilang tidak memotongnya). Sesi sependek satu pembacaan tidak dihitung.
- **Sleep tidak dihitung.** Kalau jarak dua pembacaan lebih dari tiga kali `poll_seconds` (PC
  sleep, jam PC berubah), sesi yang terbuka ditutup di pembacaan terakhirnya, lalu dimulai baru.
- **Restart Cyrene di tengah main tidak memecah sesi.** Saat berhenti, `waifu run` menutup
  sesinya di pembacaan terakhir; kalau game masih terbuka dan terakhir terlihat paling lama 5
  menit sebelum `waifu run` berikutnya siap, sesinya dilanjutkan, dan jeda restart ikut
  terhitung. Kalau `waifu run` mati mendadak, sesinya ditutup di pembacaan terakhirnya.
- **Hari dihitung WIB.** Sesi yang melewati tengah malam dihitung di kedua hari. "Minggu ini"
  sejak Senin 00.00, "bulan ini" sejak tanggal 1. Di total, waktu dua game terbuka bersamaan
  dihitung sekali.

### Pengingat tidur (pilihan)

Mati dari awal. Isi `play_time.bedtime`, misalnya `23:30`: lewat jam itu, kalau masih ada game
yang terbuka, dia bilang sekali semalam dengan lembut, tanpa ceramah:

```text
Cyrene : "Sayang, sudah lewat jam setengah dua belas, jangan terlalu malam ya."
```

- **Sekali semalam** (malamnya dari `bedtime` sampai jam 06.00), juga kalau `waifu run`
  di-restart: malam yang sudah diingatkan ditulis ke `data\playtime.json` sebelum dia bicara.
- **Tidak pernah memotong**: diucapkan di sela kalimat Master, setelah jendela lanjutan
  tertutup. Karena itu, kalau dia sedang menunggu, kalimatnya bisa terucap sesudah game baru
  saja ditutup. Tidak dikirim ke HP. Dengan [Mode game](#mode-game) `hold` atau `phone`,
  pengingat ini diucapkan seperti pengingat yang Master pasang: tidak menunggu game ditutup.
- Jamnya dari 18:00 sampai 05:59; jam lewat tengah malam (misalnya `01:00`) dihitung untuk
  malam sebelumnya.
- **Bukan untuk bangun pagi**: game yang baru dibuka mulai jam 04.00 (misalnya untuk daily
  sesudah reset jam 03.00) tidak diingatkan, karena Master baru bangun, bukan begadang. Game
  yang sudah terbuka sebelum itu tetap diingatkan. Kalau `bedtime` sesudah 04:00, batasnya jam
  `bedtime` itu sendiri.

### Pengaturan jam main

```yaml
play_time:
  enabled: true          # false: tidak mencatat dan tanpa tool (`waifu playtime` tetap membaca catatan lama)
  poll_seconds: 60       # baca daftar proses tiap N detik selama `waifu run` (15-600)
  games:                 # nama program tiap game; hanya game yang ditulis yang dihitung
    hsr: [StarRail.exe]
    genshin: [GenshinImpact.exe, YuanShen.exe]
    zzz: [ZenlessZoneZero.exe]
  bedtime: "23:30"       # pengingat tidur; kosongkan atau hapus baris ini untuk mematikannya
  keep_days: 90          # catatan lebih lama dari N hari dihapus (1-366)
  path: data/playtime.json
```

Bagian ini tidak perlu ditulis untuk memakai bawaannya (tanpa pengingat tidur); contohnya ada di
`config.example.yaml`. Jalankan ulang `waifu run` setelah mengubahnya.

### Melihat catatan: `waifu playtime`

```powershell
waifu playtime          # hari ini
waifu playtime week     # minggu ini, sejak Senin; juga yesterday dan month
```

```text
Jam main hari ini, Sabtu 17-10-2026, dari catatan Cyrene:
GAME               WAKTU           SESI
Honkai: Star Rail  2 jam 39 menit  3     sedang main
Genshin Impact     30 menit        1
Zenless Zone Zero  45 menit        1
Total              3 jam 24 menit  5
Dihitung hanya selama 'waifu run' menyala, sejak Jumat 16-10-2026 22:00; game yang terbuka tapi tidak dimainkan ikut terhitung.
```

`-` berarti tidak ada waktu di periode itu, dan `sedang main` berarti game itu terlihat di
pembacaan terakhir `waifu run`. Total adalah waktu ada game yang terbuka, jadi bisa lebih kecil
dari jumlah per game. Perintah ini jalan juga saat `play_time.enabled: false` (membaca catatan
lama) dan selagi `waifu run` berjalan; exit code 1 kalau filenya tidak bisa dibaca.

`waifu check` menampilkan `✓ Jam main: aktif (HSR, Genshin, ZZZ)` (dengan
`, pengingat tidur 23:30` kalau diisi) atau `! Jam main mati`, tanpa membaca daftar proses.
File `data\playtime.json` yang rusak dipindah ke `playtime.json.corrupt-<waktu>` (catatan baru
dimulai dari kosong), dan `check` menunjuknya sampai Master memperbaiki atau menghapusnya.

### Aman untuk anti-cheat

HSR, Genshin, dan ZZZ memakai anti-cheat (mhyprot, HoYoKProtect). Cyrene hanya membaca daftar
nama proses yang Windows sediakan untuk semua program (seperti tab Details di Task Manager), dan
tidak pernah membuka, membaca memori, atau menyentuh proses game. Tidak ada yang dipasang dan
tidak ada driver.

### Privasi jam main

Dari daftar proses hanya nama tiga game yang dipakai; nama program lain tidak disimpan, tidak
ditulis ke log, dan tidak dikirim. `data\playtime.json` hanya berisi game mana yang terbuka dari
jam berapa sampai jam berapa (`keep_days`, bawaan 90 hari) dan malam terakhir pengingat tidur
diucapkan, hanya di PC ini. Jam main tidak punya pemberitahuan HP sendiri, dan pengingat tidur
tidak dikirim ke HP. Tapi kalimat [laporan game mingguan](#laporan-game-mingguan), yang bisa
menyebut jam main minggu itu, dikirim ke `ntfy.sh` kalau notifikasi HP dan `weekly_report.push`
menyala (siapa pun yang tahu nama topiknya bisa membacanya); `weekly_report.push: false` membuat
laporannya tetap di PC. Jawaban atas pertanyaan lewat [Cyrene di HP](#cyrene-di-hp) sampai ke HP
itu lewat Tailscale. Dengan Claude, totalnya ikut ke API Anthropic sebagai hasil tool saat Master
bertanya dan di laporan game mingguan (dengan Ollama, semuanya lokal). Log biasa tidak mencatat
sesinya.

Untuk berhenti mencatat, tulis `enabled: false` di bagian `play_time`; catatan lama hilang kalau
`data\playtime.json` dihapus.

## Teman fokus

Selama `waifu run`, Cyrene bisa menemani Master fokus belajar atau kerja dengan cara pomodoro: dia
menjaga waktu blok fokus dan istirahat, diam selama blok fokus, dan memberi tahu kapan istirahat
dan kapan lanjut. Fitur ini sudah menyala dari awal (`focus.enabled: true`), dengan otak Ollama
maupun Claude, tanpa paket tambahan.

```text
Master : "Cyrene, temani aku fokus 25 menit."
Cyrene : "Siap, Sayang. Aku temani sampai jam sembilan lewat dua puluh lima."
Master : "Cyrene, pomodoro empat kali."          (empat blok 25 menit, istirahat 5 menit)
Master : "Cyrene, fokus 50 menit, istirahat 10, tiga kali."
Master : "Cyrene, sisa berapa menit?"
Master : "Cyrene, kapan selesai semuanya?"
Master : "Cyrene, hari ini sudah fokus berapa lama?"
Master : "Cyrene, udah, berhenti fokusnya."
```

Panjang blok 5-120 menit (bawaan 25), istirahat 1-60 menit (bawaan 5), dan 1-8 blok (bawaan 1).
Hanya satu sesi sekaligus: untuk mengganti panjangnya, hentikan dulu lalu mulai lagi. Dia hanya
memulai atau menghentikan sesi kalau Master minta.

### Selama blok fokus

- **Dia diam.** Yang biasanya dia ucapkan sendiri menunggu sampai istirahat: peringatan hujan,
  kabar versi baru, pengingat tidur jam main, pemberitahuan game, kode redeem, kesehatan PC,
  promo gratis, dan anggaran biaya API, juga sapaan pagi dan laporan game mingguan. Saat istirahat
  dimulai, pemberitahuan yang tertunda diucapkan sekaligus sesudah kalimat istirahatnya; sapaan
  pagi atau laporan mingguan menyusul sesudahnya.
- **Sapaan "sudah kembali" dari maskot avatar dilewati**, tidak disimpan untuk istirahat: kalau
  Master lama tidak menyentuh mouse lalu kembali di tengah blok, dia tetap diam. Elusan, colekan,
  dan klik dua kali tetap dia jawab seperti biasa.
- **Pengingat dan jadwal yang Master pasang sendiri tetap berbunyi tepat waktu** (lihat
  [Jadwal, pengingat, dan rutinitas](#jadwal-pengingat-dan-rutinitas)).
- **Master tetap bisa bicara dengannya** seperti biasa: panggil namanya, klik dua kali avatarnya,
  atau lewat [Cyrene di HP](#cyrene-di-hp).
- [Pengingat istirahat](#pengingat-istirahat) tidak muncul selama blok fokus dan tidak menghitung
  apa pun; sesudah blok hitungannya mulai dari nol.
- [Pengingat waktu sholat](#waktu-sholat), kalau dinyalakan, tetap diucapkan tepat waktu seperti
  pengingat yang Master pasang sendiri.
- Notifikasi HP dari fitur lain (misalnya game atau kesehatan PC) tetap terkirim seperti biasa;
  hanya suaranya di PC yang menunggu. Peringatan hujan yang tertunda sampai hujannya mulai tidak
  diucapkan lagi. Pemberitahuan lain, termasuk pengingat resin atau daily dan GPU panas, tetap
  diucapkan saat istirahat, berapa lama pun bloknya (kalau game dibuka di tengah blok, lihat
  [Mode game](#mode-game)).

### Kalimatnya

```text
Cyrene : "Sayang, dua puluh lima menit selesai. Istirahat lima menit dulu ya."   (akhir blok)
Cyrene : "Yuk, lanjut fokus lagi."                                             (akhir istirahat)
Cyrene : "Sayang, fokusnya selesai. Hebat, total satu jam empat puluh menit!"  (akhir sesi)
```

Kalimat ini tidak dikirim ke HP, kecuali `focus.push: true` dan notifikasi HP menyala.

### Sesi berakhir saat restart

Sesinya hanya ada di `waifu run` yang sedang berjalan: kalau `waifu run` berhenti, sesinya selesai
tanpa kata dan tidak dilanjutkan saat dia jalan lagi. Blok yang sudah selesai selalu tercatat.
Bagian blok yang sedang berjalan (minimal satu menit) hanya ikut dicatat kalau `waifu run`
dihentikan dengan Ctrl+C. Menutup jendela konsol "Cyrene" (cara menghentikan autostart), sign out,
atau mematikan PC langsung mengakhiri prosesnya, jadi bagian blok itu hilang. Kalau PC sleep
melewati akhir blok atau istirahat lebih dari 2 menit, sesinya juga selesai diam-diam, dan blok
yang sedang berjalan tidak dicatat. `waifu chat` tidak punya timer fokus.

### Pengaturan teman fokus

```yaml
focus:
  enabled: true          # false: tanpa timer fokus (`waifu focus` tetap membaca catatan lama)
  push: false            # true: kalimat akhir blok, istirahat, dan sesi juga dikirim ke HP (butuh notify)
  keep_days: 90          # catatan lebih lama dari N hari dihapus (1-366)
  path: data/focus.json
```

Bagian ini tidak perlu ditulis untuk memakai bawaannya; contohnya ada di `config.example.yaml`.
Panjang blok dan istirahat diminta lewat suara, bukan di sini. Jalankan ulang `waifu run` setelah
mengubahnya.

### Melihat catatan: `waifu focus`

```powershell
waifu focus
```

```text
Fokus hari ini, Sabtu 17-10-2026: 1 jam 40 menit dalam 4 blok
Fokus minggu ini, sejak Senin 12-10-2026: 2 jam 40 menit dalam 5 blok
Dicatat hanya selama 'waifu run' menyala; sesi fokus dimulai dan dihentikan lewat Cyrene.
```

Perintah ini hanya membaca catatan: tidak bisa memulai, menghentikan, atau melihat sesi yang
sedang berjalan (tanya Cyrene saja). Jalan juga saat `focus.enabled: false` (membaca catatan
lama) dan selagi `waifu run` berjalan; exit code 1 kalau filenya tidak bisa dibaca.

`waifu check` menampilkan `✓ Teman fokus: aktif` (dengan `, juga ke HP` kalau `focus.push` dan
notifikasi HP menyala) atau `! Teman fokus mati`. File `data\focus.json` yang rusak dipindah ke
`focus.json.corrupt-<waktu>` (catatan baru dimulai dari kosong), dan `check` menunjuknya sampai
Master memperbaiki atau menghapusnya.

### Privasi teman fokus

`data\focus.json` hanya berisi kapan tiap blok fokus mulai dan selesai (`keep_days`, bawaan 90
hari), hanya di PC ini; apa yang Master kerjakan tidak dicatat. Dengan Claude, panjang sesi,
jamnya, dan waktu fokus hari ini dan minggu ini ikut ke API Anthropic sebagai hasil tool saat
Master bertanya (dengan Ollama, semuanya lokal). Kalimatnya ke `ntfy.sh` hanya kalau
`focus.push` dan notifikasi HP menyala. Log INFO mencatat panggilan tool `focus_timer` dengan
panjang sesinya, dan kalimat Master yang terdengar, seperti biasa.

Untuk mematikannya, tulis `enabled: false` di bagian `focus`; catatan lama hilang kalau
`data\focus.json` dihapus.

## Mode game

Selama HSR, Genshin, atau ZZZ terbuka, Cyrene bisa diam supaya tidak memotong game atau obrolan
voice chat Master, dan bisa menjalankan rutinitas (misalnya meredupkan lampu) saat Master mulai
dan selesai main. Dia tahu ada game yang terbuka dari [Jam main](#jam-main), jadi
`play_time.enabled` harus menyala (bawaannya menyala) dan hanya game di `play_time.games` yang
dihitung. Mati dari awal; untuk menyalakannya, tulis bagian `game_mode` di `config.yaml`:

```yaml
game_mode:
  announcements: hold    # normal (bawaan), hold, atau phone
  on_start: redup        # rutinitas saat game pertama dibuka (boleh tidak ditulis)
  on_stop: terang        # rutinitas saat game terakhir ditutup (boleh tidak ditulis)
```

Jalankan ulang `waifu run` setelah mengubahnya. `waifu chat` tidak punya mode game.

### Tiga pilihan pengumuman

- **`normal`** (bawaan): seperti sebelumnya, pemberitahuannya tetap diucapkan saat main.
- **`hold`**: selama ada game yang terbuka, yang biasanya dia ucapkan sendiri menunggu:
  peringatan hujan, kabar versi baru, pemberitahuan game, kode redeem, kesehatan PC, promo
  gratis, dan anggaran biaya API, juga sapaan pagi, laporan game mingguan, dan pertanyaan malam
  jurnal. Setelah game terakhir ditutup, pemberitahuan yang tertunda diucapkan sekaligus; sapaan
  pagi, laporan mingguan, atau pertanyaan jurnal menyusul satu per satu. Pemberitahuan yang
  tertunda dibuka dengan "Tadi selama main ada kabar.", dan yang sama persis hanya diucapkan
  sekali.
- **`phone`**: pemberitahuan yang memang dikirim ke HP (notifikasi HP menyala; untuk peringatan
  hujan dan kabar versi baru juga `push`-nya) tidak diucapkan sama sekali selama main, karena
  sudah ada di HP. Yang tidak dikirim ke HP ditahan seperti `hold`, begitu juga sapaan pagi,
  laporan mingguan, dan pertanyaan jurnal: tidak ada yang dikirim ke HP sebagai gantinya. Tanpa
  notifikasi HP, `phone` sama dengan `hold`, dan `waifu check` memperingatkannya.

### Yang tetap berbunyi

- **Pengingat dan jadwal yang Master pasang sendiri**, tepat waktu, juga kalimat
  [Teman fokus](#teman-fokus) dan [pengingat saat game dibuka](#pengingat-saat-game-dibuka).
- **Pengingat tidur jam main** (`play_time.bedtime`): hanya muncul selagi game terbuka, jadi
  dengan `hold` atau `phone` dia mengucapkannya seperti pengingat Master, tanpa menunggu game
  ditutup (juga selagi jendela lanjutan terbuka atau di tengah blok fokus).
- **[Pengingat istirahat](#pengingat-istirahat) dengan `breaks.during_games: true`**: diucapkan
  selagi main seperti pengingat Master. Tanpa itu, kalimatnya ditahan seperti pemberitahuan lain.
- **[Pengingat waktu sholat](#waktu-sholat)**, kalau dinyalakan: tepat waktu seperti pengingat
  Master, juga dengan `phone` (yang sudah ke HP tetap diucapkan).
- **Master tetap bisa bicara dengannya** seperti biasa: panggil namanya, klik dua kali avatarnya,
  atau lewat [Cyrene di HP](#cyrene-di-hp). Elusan dan colekan di avatar tetap dijawab; hanya
  sapaan "sudah kembali" yang dilewati selama game menahan pengumumannya.

### Yang dilewati

- Peringatan hujan yang tertahan sampai hujannya mulai tidak diucapkan lagi (notifikasi HP-nya,
  kalau menyala, sudah terkirim saat itu).
- Sapaan pagi yang tertahan sampai `briefing.until`, dan laporan mingguan atau pertanyaan jurnal
  yang tertahan sampai tengah malam, dilewati hari itu, seperti saat tertahan blok fokus.
- Pengingat game tentang keadaan game (stamina atau resin penuh, daily, mingguan, ekspedisi)
  yang tertahan lebih dari `gacha.poll_minutes` (bawaan 15 menit) dilewati: game yang baru
  dimainkan mungkin sudah mengubahnya. Tanpa notifikasi HP, pengingat untuk game yang tidak
  dimainkan juga ikut terlewat, dan tidak diulang. Masalah HoYoLAB (misalnya cookie) dan
  check-in tetap diucapkan.
- Pemberitahuan lain tetap disimpan sampai game ditutup, berapa lama pun, selama `waifu run`
  tetap berjalan: game yang dibiarkan terbuka semalaman (diminimalkan pun) menahannya semalaman.
- Pemberitahuan yang masih tertahan saat `waifu run` dihentikan, termasuk kalau PC dimatikan
  dalam 1-2 menit sesudah game ditutup (sebelum tahanannya lepas), tidak diucapkan dan tidak
  diulang nanti. Kalau notifikasi HP menyala, yang dikirim ke HP tetap ada di HP.
- Pemberitahuan GPU panas yang tertahan sampai sejam dilewati: dari main lama yang membuat GPU
  terus panas, paling banyak satu (yang terakhir, kalau belum sejam) diucapkan sesudah game
  ditutup.
- [Pengingat istirahat](#pengingat-istirahat) yang tertahan lebih dari
  `breaks.idle_reset_minutes` (bawaan 5 menit) dilewati; yang berikutnya datang
  `breaks.every_minutes` (bawaan sejam) kemudian kalau Master masih di depan layar.
- Pengingat game dan GPU panas hanya dilewati karena game: kalau game dibuka di tengah blok
  fokus, yang sudah tertahan sejak awal blok ikut dilewati, tapi yang baru masuk sesudah game
  ditutup tetap ditunggu sampai istirahat, dan blok fokus tanpa main game menahannya sampai
  istirahat, seperti sebelum ada mode game.
- Dengan `phone`, kalau notifikasi HP gagal terkirim (misalnya internet putus), pemberitahuan
  itu juga tidak diucapkan.

### Rutinitas saat mulai dan selesai main

`on_start` dan `on_stop` adalah nama rutinitas yang Master tulis di bagian `routines` (lihat
[Menulis rutinitas di config.yaml](#menulis-rutinitas-di-configyaml)), misalnya:

```yaml
routines:
  redup:
    description: Lampu redup untuk main
    steps:
      - {device: lampu_kamar, command: brightness, value: 20}
  terang:
    description: Lampu terang lagi
    steps:
      - {device: lampu_kamar, command: brightness, value: 100}
```

- `on_start` jalan sekali saat game pertama dibuka (sebelumnya tidak ada game yang terbuka), dan
  `on_stop` sekali saat game terakhir ditutup. Pindah dari HSR ke Genshin tanpa jeda tidak
  menjalankan apa-apa.
- Keadaan ruangan sebelum main tidak disimpan: `on_stop` menjalankan rutinitasnya apa adanya.
  `brightness` juga menyalakan lampu, jadi `terang` di contoh ini menyalakan lampu yang tadinya
  mati sebelum main, dengan terang penuh.
- Diam-diam: dia tidak bicara. Langkah yang gagal hanya dicatat di log (`The game start routine
  'redup' failed at step 1 ...`).
- Nama yang tidak ada di `routines` membuat `config.yaml` ditolak saat start, begitu juga mode
  game dengan `play_time.enabled: false`. Rutinitas dengan PC atau colokan PC memang sudah
  ditolak; langkah dengan `after_minutes` masuk jadwal seperti biasa.

### Kapan dihitung mulai dan selesai

- **Mulai** pada pengecekan daftar proses pertama yang melihat game: paling lama
  `play_time.poll_seconds` (60 detik) sesudah game dibuka.
- **Selesai** setelah dua pengecekan berturut-turut tidak melihatnya: sekitar 1-2 menit sesudah
  game ditutup. Alt-tab, game yang diminimalkan, atau game dan launcher yang di-restart dalam
  semenit tidak dihitung selesai. Game yang terbuka tapi diminimalkan atau ditinggal tetap
  dihitung main.
- Restart `waifu run` di tengah main tidak menjalankan `on_start` lagi, dan menghentikan
  `waifu run` tidak menjalankan `on_stop`. Kalau game ditutup selagi `waifu run` mati, `on_stop`
  tidak jalan: lampunya tetap redup sampai Master mengubahnya.
- Game yang crash tepat saat dibuka (terlihat sekali saja) menjalankan `on_start` lalu `on_stop`,
  jadi ruangannya diatur oleh `on_stop`, bukan dikembalikan ke keadaan sebelum main.
- Kalau daftar proses tidak bisa dibaca lebih dari tiga pengecekan, pengumuman tidak ditahan lagi
  sampai bisa dibaca kembali. Sesudah PC bangun dari sleep dengan game terbuka, pengumuman yang
  tertunda bisa keluar sebelum pengecekan pertama sesudah bangun.

`waifu check` menampilkan salah satu baris ini (rutinitasnya hanya kalau ditulis):

```text
✓ Mode game: pengumuman ditahan selama main; rutinitas redup saat game dibuka, terang saat game ditutup
✓ Mode game: pengumuman ke HP selama main
✓ Mode game: pengumuman tetap diucapkan saat main; rutinitas redup saat game dibuka
! Mode game: ke HP, tapi notify mati: pengumuman ditahan sampai game ditutup
! Mode game mati
```

### Privasi mode game

Tidak ada yang baru disimpan atau dikirim: apakah game sedang terbuka hanya diingat selama
`waifu run` berjalan, dan kapan mulai dan selesai main hanya dicatat di log DEBUG. Rutinitasnya
berjalan di perangkat Master sendiri seperti rutinitas lain (langkah yang gagal dicatat di log
dengan langkahnya). Dengan `phone`, tidak ada notifikasi HP tambahan: yang sampai ke HP hanya
yang memang sudah dikirim fitur lain.

Untuk mematikannya, hapus bagian `game_mode` atau tulis `announcements: normal` tanpa
`on_start` dan `on_stop`.

## Pengingat saat game dibuka

Kadang yang perlu diingat bukan jamnya, tapi saat main berikutnya. Cyrene menyimpan pengingat
seperti itu dan mengucapkannya saat game itu benar-benar baru dibuka. Fitur ini sudah menyala
dari awal (`app_reminders.enabled: true`), dengan otak Ollama maupun Claude. Dia tahu game dibuka
dari [Jam main](#jam-main), jadi `play_time.enabled` harus menyala (bawaannya menyala), dan hanya
game di `play_time.games` (HSR, Genshin, ZZZ) yang bisa.

```text
Master : "Cyrene, pas buka HSR nanti, ingetin aku farming relic."
Cyrene : "Oke, nanti pas kamu buka HSR kuingatkan ya."
          ... malamnya HSR dibuka ...
Cyrene : "Master, kamu baru buka HSR, jangan lupa farming relic."
Master : "Cyrene, tiap buka Genshin ingetin cek ekspedisi."
Master : "Cyrene, nanti kalau buka ZZZ ingetin claim battle pass."
Master : "Cyrene, pengingat gameku apa aja?"
Master : "Cyrene, pengingat HSR-ku apa aja?"
Master : "Cyrene, hapus pengingat Genshin yang ekspedisi."
```

### Sekali atau tiap kali

- **Sekali** (bawaan): diucapkan saat game itu dibuka berikutnya, lalu hilang.
- **Tiap kali**: kalau Master bilang "tiap" atau "setiap kali", diucapkan setiap game itu dibuka
  sampai Master menghapusnya.
- Beberapa pengingat untuk game yang sama diucapkan dalam satu kalimat: "Master, kamu baru buka
  ZZZ, jangan lupa claim battle pass dan cek toko."
- Hal yang jangan dilakukan diucapkan sesudah "ingat ya": "pas buka Genshin nanti ingetin aku
  jangan top up dulu" jadi "Master, kamu baru buka Genshin, ingat ya, jangan top up dulu."
- Kalau game-nya sedang terbuka saat Master memintanya, pengingatnya disimpan untuk saat game itu
  dibuka lagi nanti, dan dia bilang begitu; tidak diucapkan sekarang.
- Paling banyak 30 pengingat; yang ke-31 ditolak sampai ada yang dihapus. Pengingat yang terlihat
  seperti rahasia (kata sandi, PIN, token, nomor kartu) tidak disimpan.
- Untuk jam tertentu ("ingetin aku jam 9 malam") tetap pakai
  [pengingat biasa](#jadwal-pengingat-dan-rutinitas).

### Kapan diucapkan

- **Hanya saat game benar-benar baru dibuka** selagi `waifu run` menyala: paling lama
  `play_time.poll_seconds` (60 detik) sesudah programnya muncul, jadi biasanya selagi layar
  loading.
- **Bukan untuk game yang sudah terbuka** saat `waifu run` mulai (misalnya autostart sesudah
  login, atau restart Cyrene di tengah main), dan bukan saat PC bangun dari sleep atau jam PC
  berubah: pengingatnya menunggu pembukaan berikutnya. Game yang dibuka selagi `waifu run` masih
  memuat model suaranya, atau tepat sesudah PC bangun, juga dianggap sudah terbuka.
- Game yang ditutup lalu dibuka lagi dalam sekitar 1-2 menit (misalnya launcher yang restart)
  masih sesi yang sama: tidak diucapkan lagi.
- Diucapkan **seperti pengingat yang Master pasang sendiri**: di sela kalimat, juga selama blok
  [Teman fokus](#teman-fokus) dan selama [Mode game](#mode-game) menahan pengumumannya (`hold`
  maupun `phone`). Kalau notifikasi HP menyala, kalimatnya juga dikirim ke HP, seperti pengingat
  jadwal.
- Dengan [Mode tamu](#mode-tamu), pengingat yang jatuh waktunya tetap diucapkan (seperti
  pengingat lain yang Master pasang), tapi daftar, menambah, dan menghapusnya lewat suara
  ditolak.
- Pengingat sekali dihapus begitu dia mengantrekannya: kalau `waifu run` dihentikan sebelum dia
  sempat bicara, pengingatnya hilang (yang dikirim ke HP tetap ada). Kalau filenya tidak bisa
  ditulis saat itu, pengingatnya tetap diucapkan dan bisa terulang di pembukaan berikutnya.

### Dari terminal: `waifu pengingat-game`

```powershell
waifu pengingat-game            # semua pengingat game, yang paling lama dibuat dulu
waifu pengingat-game hapus g2   # hapus satu (boleh juga: hapus 2)
```

```text
ID  GAME     KAPAN        PENGINGAT
g1  HSR      sekali       farming relic
g2  Genshin  tiap dibuka  cek ekspedisi
```

Menambah pengingat hanya lewat suara atau `waifu chat`. Kedua perintah jalan juga saat fiturnya
mati, dan Cyrene yang sedang berjalan langsung melihat perubahannya. `waifu check` menampilkan
`✓ Pengingat saat game dibuka: 2 tersimpan`, atau `! Pengingat saat game dibuka mati` (juga
kalau `play_time` mati). File `data\app_reminders.json` yang rusak dipindah ke
`app_reminders.json.corrupt-<waktu>` (daftar baru dimulai dari kosong), dan `check` menunjuknya
sampai Master memperbaiki atau menghapusnya.

Untuk mematikannya, tulis di `config.yaml`:

```yaml
app_reminders:
  enabled: false           # tanpa tool dan tidak ada yang diucapkan (`waifu pengingat-game` tetap jalan)
  path: data/app_reminders.json
```

### Privasi pengingat game

`data\app_reminders.json` hanya berisi game, teks pengingatnya, sekali atau tiap kali, dan kapan
dibuat, hanya di PC ini. Kalau notifikasi HP menyala, pengingat yang jatuh waktunya dikirim ke
`ntfy.sh` seperti pengingat jadwal (siapa pun yang tahu nama topiknya bisa membacanya). Dengan
Claude, teksnya ikut ke API Anthropic saat Master membuat, melihat, atau menghapusnya (dengan
Ollama, semuanya lokal). Seperti pengingat jadwal, log biasa mencatat teksnya saat Master
membuatnya (baris panggilan tool), tapi saat diucapkan hanya berapa pengingatnya, bukan isinya.

## Pemantau internet

Saat internet putus, Cyrene dulu hanya bisa bilang tidak bisa terhubung ke server Claude, dan
Master tidak tahu yang salah Wi-Fi, provider, DNS, atau HoYoverse. Sekarang selama `waifu run`
menyala dia memantau koneksi PC: router, internet di luar, dan (selagi HSR, Genshin, atau ZZZ
dimainkan) server HoYoLAB/HoYoverse. Dia bilang sekali saat internet nyambung lagi sesudah putus,
menjawab "internetnya kenapa?" sendiri tanpa Claude (jadi juga saat internet mati), dan
menyimpan riwayat putusnya seminggu. Fitur ini sudah menyala dari awal (`network.enabled:
true`), dengan otak Ollama maupun Claude, tanpa paket tambahan dan tanpa hak administrator.

```text
Master : "Cyrene, internetnya kenapa?"
Cyrene : "Master, internetnya putus: routernya tersambung, tapi internet di luar tidak bisa
          dihubungi. Putusnya sejak jam 21.03. Coba restart modem atau routernya. Kalau tetap
          putus, mungkin ada gangguan dari provider."
          ... jam 21.15 internetnya kembali ...
Cyrene : "Master, internetnya sudah nyambung lagi, tadi putus sekitar 12 menit, sejak jam 21.03."
Master : "Cyrene, kok lemot?"
Cyrene : "Master, internetnya lagi lambat, ping-nya sekitar 350 milidetik. Mungkin ada yang
          sedang download, atau jaringannya lagi padat."
Master : "Cyrene, kok lemot lagi?"
Cyrene : "Master, ping internetnya normal, sekitar 24 milidetik. Kalau masih terasa lemot,
          mungkin kecepatannya yang turun, atau dari aplikasinya."
Master : "Cyrene, server HoYo down?"
Cyrene : "Master, ping internetnya normal, sekitar 24 milidetik. Server HoYoLAB dan HoYoverse
          menjawab, sekitar 45 milidetik. Itu server HoYoLAB dan HoYoverse, bukan server game-nya."
Master : "Cyrene, ping-ku berapa?"
Master : "Cyrene, tadi internet putus berapa kali?"
```

### Yang dicek

- **Router:** ping ke gateway yang dipakai Windows (`hosts.gateway: auto`). Banyak router tidak
  membalas ping, jadi router yang tidak pernah membalas tidak pernah disalahkan: "router tidak
  terjangkau" hanya kalau Windows tidak punya rute (Wi-Fi atau kabel lepas), atau router yang
  tadi membalas sekarang diam.
- **Internet:** ping ke 1.1.1.1 dan 8.8.8.8 (`hosts.anchors`), dan kalau ping diblokir firewall,
  sambungan ke port 443-nya. Putus hanya kalau keduanya diam.
- **DNS:** mencari `www.google.com` (`hosts.dns`) langsung ke server DNS, melewati cache Windows
  yang bisa menyembunyikan DNS yang mati. Dicek saat Master bertanya, bukan tiap menit.
- **Lambat:** ping di atas `slow_ms` (200 ms). Saat Master bertanya, yang dinilai dan disebut
  adalah ping cek saat itu; untuk catatan pemantau, median beberapa cek terakhir (`slow_window`,
  5), jadi satu cek lambat tidak dihitung. Yang diukur hanya ping (jeda), bukan kecepatan
  download, jadi dengan ping normal dia tidak bilang internetnya pasti lancar.
- **Server HoYoLAB/HoYoverse:** sambungan TCP lewat IPv4, seperti ping-nya (tanpa mengirim apa
  pun), ke server web yang dipakai
  [teman main gacha](#teman-main-gacha-hoyoverse) (`hosts.hoyo`), hanya selagi game dimainkan
  (paling sering tiap 150 detik) dan saat Master bertanya. Ini **bukan ping server game**: alamat
  server game-nya tidak dipublikasikan.
- Satu cek paling lama sekitar 3 detik.

### Kapan dia bicara sendiri

- **Hanya sekali saat nyambung lagi**, sesudah putus paling sedikit 3 menit
  (`back_after_minutes`), paling banyak 3 kali sehari (`max_back_per_day`). Hitungannya disimpan
  di file sebelum dia bicara, jadi restart tidak mengulangnya. Putus lebih dari sejam disebut
  dalam jam dan menit ("sekitar 2 jam 5 menit"), seperti di rekap.
- **Putus berarti dua cek berturut-turut gagal** (cek tiap 60 detik, `poll_seconds`), jadi ping
  yang hilang sekali tidak dihitung. Mulainya dicatat di cek gagal pertama.
- Diucapkan seperti pengumuman lain: sesudah jendela lanjutan tertutup, ditahan blok
  [Teman fokus](#teman-fokus) dan [Mode game](#mode-game) (`hold` maupun `phone`), dan dibuang
  kalau 30 menit kemudian belum sempat diucapkan. Tidak dikirim ke HP.
- **Bukan saat PC bangun dari sleep, jam PC berubah, atau `waifu run` baru mulai:** Wi-Fi butuh
  waktu menyambung lagi, jadi dua cek sesudahnya tidak dihitung. Putus yang masih berjalan saat PC
  tidur dicatat sampai cek gagal terakhirnya, tanpa kalimat, karena kapan internetnya kembali
  tidak diketahui.

### Ditanya, juga tanpa internet

"Internetnya kenapa?", "internet mati ya?", "kok lemot?", "koneksinya gimana?", "cek wifi",
"WiFi-nya putus lagi?", "server HoYo down?", "server HoYoLAB bermasalah?": kalau kalimatnya hanya
itu (nama dia, "Master", dan kata seperti "dong", "ya", "sih", "coba" boleh ada), dia mengecek
saat itu (sekitar 3 detik, avatarnya terlihat berpikir) dan menjawab sendiri tanpa otak, jadi
juga saat Claude tidak bisa dihubungi. "Ulangi" mengulang jawabannya. Kalimat lain ("internetnya
kenapa ya, restart router dong") dan pertanyaan tanpa subjek ("mati lagi?") tetap ke otak seperti
biasa.

- Setiap masalah yang bisa Master atasi diakhiri satu saran singkat: router tidak terjangkau
  "coba cek Wi-Fi, kabel, atau routernya"; internet putus "coba restart modem atau routernya,
  kalau tetap putus mungkin ada gangguan dari provider"; DNS bermasalah "coba restart routernya,
  atau ganti DNS-nya ke Cloudflare atau Google"; lambat "mungkin ada yang sedang download, atau
  jaringannya lagi padat".

- Tanpa internet yang bicara [suara cadangan offline](#suara-cadangan-offline) (suara Windows):
  selama cek terakhir gagal, dia langsung memakainya tanpa menunggu edge-tts gagal dulu, dan
  begitu cek berikutnya berhasil, edge-tts dipakai lagi. Tanpa suara Windows bahasa Indonesia dia
  tidak bisa bicara saat offline (`waifu voices` memberi tahu).
- Kalau giliran Claude gagal karena tidak bisa terhubung, permintaan maafnya menyebut putusnya:
  "Maaf Master, aku tidak bisa terhubung ke server Claude. Internetnya lagi putus sejak jam
  21.03." Sebelum putusnya dipastikan (dua cek gagal berturut-turut), selama cek terakhir gagal:
  "Sepertinya internetnya lagi putus."
- Dengan internet menyala, otaknya punya tool `network_status` untuk detail (ping saat itu,
  router, DNS, server HoYo, berapa kali putus hari ini). Dia tidak pernah menyebut alamat IP.
- Lewat [HP](#cyrene-di-hp) dan `waifu chat`, pertanyaan ini ke otak seperti biasa.
- [Rekap hari ini](#rekap-hari-ini) dan [laporan game mingguan](#laporan-game-mingguan) menyebut
  berapa kali dan berapa lama internet putus, hanya kalau pernah.

### Dari terminal: `waifu internet`

```powershell
waifu internet
```

```text
Memeriksa internet sekarang, tunggu sebentar...
✓ Status: lancar
  Router 192.168.1.1: menjawab dalam <1 ms
  Internet (1.1.1.1, 8.8.8.8): menjawab dalam 24 ms
  DNS (www.google.com): menjawab dalam 18 ms
  Server HoYoLAB/HoYoverse: 3 dari 3 menjawab, sekitar 45 ms (bukan ping server game)
Hari ini: putus 2 kali, total 16 menit: 21.03-21.15, 22.30-22.34
Putusnya dicatat hanya selama 'waifu run' menyala.
```

Kalau ada masalah, baris `  Saran: ...` di bawah hasil cek memberi saran yang sama dengan
jawabannya. Perintah ini jalan juga saat pemantaunya mati, tidak menulis apa pun, dan exit
code-nya 1 kalau riwayatnya tidak bisa dibaca. Alamat router hanya tampil di sini. `waifu check` menampilkan
`✓ Pemantau internet: aktif, tiap 60 detik` atau `! Pemantau internet mati`, tanpa mengecek
koneksi. File `data\network.json` yang rusak dipindah ke `network.json.corrupt-<waktu>` (riwayat
baru dimulai dari kosong), dan `check` menunjuknya.

### Pengaturan pemantau internet

Tidak perlu ditulis untuk memakai bawaannya (contohnya ada di `config.example.yaml`; `waifu run`
perlu dijalankan ulang setelah mengubahnya):

```yaml
network:
  enabled: true          # false: tanpa pemantau, tanpa jawaban lokal, dan tanpa tool (`waifu internet` tetap jalan)
  poll_seconds: 60       # cek router dan internet tiap N detik (30-600)
  hoyo_poll_seconds: 150 # cek server HoYo paling sering tiap N detik, hanya selama game dimainkan (60-1800)
  slow_ms: 200           # lambat: ping di atas N milidetik (saat ditanya ping saat itu, untuk pemantau median beberapa cek terakhir)
  slow_window: 5         # ... median pemantau dari N cek terakhir
  announce_back: true    # false: tidak bilang saat internet nyambung lagi
  back_after_minutes: 3  # bilang hanya kalau putusnya paling sedikit N menit
  max_back_per_day: 3    # paling banyak N kali sehari
  keep_days: 7           # riwayat putus disimpan N hari
  path: data/network.json
  hosts:
    gateway: auto        # router; auto = yang dipakai Windows, atau alamat IPv4 seperti 192.168.1.1
    anchors: [1.1.1.1, 8.8.8.8]
    dns: www.google.com
    hoyo: [sg-public-api.hoyolab.com, bbs-api-os.hoyolab.com, public-operation-hk4e-sg.hoyoverse.com]
```

### Batasan pemantau internet

- Hanya selama `waifu run` menyala.
- Putus diketahui satu sampai dua menit sesudah mulai, dan nyambungnya paling lama satu cek
  kemudian. Putus yang mulai dalam dua cek sesudah sleep, perubahan jam, atau `waifu run` mulai
  baru dihitung dari cek ketiga.
- Ping dibulatkan ke milidetik; router di rumah sering terbaca `<1 ms`.
- Dengan VPN, yang terlihat sebagai router adalah ujung VPN-nya.
- Firewall atau router yang memblokir ping dan port 443 ke 1.1.1.1 dan 8.8.8.8 membuat internet
  terlihat putus; ubah `hosts.anchors` kalau begitu.

### Privasi pemantau internet

`data\network.json` hanya berisi kapan internet putus dan jenisnya (router atau internet), hanya
di PC ini, selama `keep_days` hari. Ceknya ke router, ke 1.1.1.1 dan 8.8.8.8 (ping, dan port 443
kalau ping diblokir), ke server DNS untuk `www.google.com`, dan selagi game dimainkan ke server
HoYoLAB/HoYoverse (hanya membuka sambungan, tanpa mengirim data). Kalimatnya dan tool-nya tidak
pernah menyebut alamat; alamat router hanya di `waifu internet` dan log DEBUG. Dengan Claude, hasil
`network_status` (status, milidetik, jumlah putus) ikut ke API Anthropic saat ditanya. Tool ini
tetap terbuka di [mode tamu](#mode-tamu): isinya bukan hal pribadi.

## Jurnal harian

Cyrene bisa menyimpan jurnal harian Master: catatan singkat per tanggal, dengan kata-kata Master
sendiri. Fitur ini sudah menyala dari awal (`journal.enabled: true`), dengan otak Ollama maupun
Claude, tanpa paket tambahan. Pertanyaan malamnya mati sampai `journal.ask_at` diisi.

```text
Master : "Cyrene, catat di jurnal: hari ini laporannya selesai dan aku main HSR sama Rina."
Cyrene : "Sudah kucatat di jurnal hari ini, Sayang."
Master : "Cyrene, tulis di jurnal, tadi siang hujan deras."    (hari yang sama: catatan kedua)
Master : "Cyrene, hari ini aku nulis apa di jurnal?"
Master : "Cyrene, kemarin aku nulis apa di jurnal?"
Master : "Cyrene, jurnal minggu ini apa aja?"
Master : "Cyrene, jurnal tanggal 12 Oktober isinya apa?"
```

- Satu hari bisa berisi beberapa catatan (bawaan paling banyak 20, masing-masing paling panjang
  500 karakter), disimpan berurutan. Catatan yang sama persis di hari yang sama hanya disimpan
  sekali.
- Kalau Master bercerita lewat tengah malam tentang hari yang baru lewat, dia mencatatnya di
  tanggal kemarin.
- Saat ditanya, dia merangkum catatannya dalam satu-dua kalimat; dia membacakan catatan kata per
  kata hanya kalau Master minta. Untuk minggu atau bulan yang panjang, dia hanya membaca 40
  catatan terbaru.
- Rahasia seperti kata sandi, PIN, OTP, token, atau nomor kartu selalu ditolak.
- Dia hanya menulis atau membaca jurnal kalau Master minta, atau saat Master menjawab pertanyaan
  malamnya: tidak pernah di sapaan pagi, laporan game mingguan, atau notifikasi HP. Isi jurnal
  juga tidak masuk ke [ingatannya](#ingatan-cyrene) kecuali Master minta.

### Pertanyaan malam (pilihan)

Isi `journal.ask_at` (misalnya `21:30`), lalu jalankan ulang `waifu run`. Sekali sehari, mulai jam
itu sampai tengah malam, saat dia tidak sedang diajak bicara, dia bertanya dengan hangat bagaimana
hari Master. Selagi Master mengobrol lewat [Cyrene di HP](#cyrene-di-hp), pertanyaannya menunggu
sampai HP sepi sekitar 10 menit (`assistant.session_idle_minutes`), supaya obrolan di HP tidak
terputus:

```text
Cyrene : "Sayang, bagaimana harimu hari ini?"
Master : "Capek, tapi laporannya akhirnya selesai."   (langsung, tanpa memanggil namanya)
Cyrene : "Sudah kucatat. Istirahat yang cukup ya."
```

- Jawab dalam beberapa detik setelah dia selesai bertanya (`assistant.followup_seconds`, bawaan 8
  detik), supaya tidak perlu memanggil namanya. Setelah itu, panggil namanya dulu ("Cyrene,
  hari ini ...") selama obrolannya masih berlangsung (sekitar 10 menit).
- Satu ucapan paling lama 15 detik, jadi cerita panjang tercatat sebagai beberapa catatan, dan
  kata-kata yang diucapkan selagi dia menjawab tidak terdengar. Untuk cerita panjang, pakai
  `waifu jurnal add` atau chat di [Cyrene di HP](#cyrene-di-hp).
- Kalau hari ini sudah ada catatan, dia menanyakan apakah ada yang mau ditambahkan.
- Kalau Master tidak menjawab atau bilang tidak mau cerita, tidak ada yang dicatat, dan dia tidak
  bertanya lagi hari itu, juga setelah `waifu run` dimulai ulang.
- Pertanyaannya tidak dikirim ke HP. Selama [blok fokus](#teman-fokus), pertanyaannya menunggu
  sampai istirahat. Kalau PC baru menyala setelah jam itu, dia bertanya saat itu, asal belum
  tengah malam. `waifu chat` tidak pernah bertanya.
- Pertanyaan yang masih tertahan sampai tengah malam (blok fokus, obrolan lewat HP, atau Master
  masih mengobrol dengannya) dilewati malam itu, supaya dia tidak bertanya tentang "hari ini"
  di tanggal yang baru. Log mencatatnya dengan `Not saying a request held past`.
- Kalau dia bertanya tepat sebelum tengah malam dan Master menjawab sesudahnya, jawabannya tetap
  dicatat di tanggal dia bertanya, bukan tanggal yang baru. Dalam sekitar 10 menit sejak
  pertanyaannya (`assistant.session_idle_minutes`) ini pasti; lewat dari itu, tanggalnya dia
  tentukan sendiri dari cerita Master, seperti cerita lewat tengah malam lainnya.

### Menghapus catatan

Menghapus jurnal tidak bisa lewat suara, karena salah dengar tidak bisa dibatalkan: kalau Master
minta, dia memberi tahu caranya. Hapus semua catatan satu hari di terminal:

```powershell
waifu jurnal forget 2026-10-16          # hanya menghitung
waifu jurnal forget 2026-10-16 --yes    # benar-benar menghapus
```

Untuk mengubah atau menghapus satu catatan saja, edit `data\journal.json` dengan Notepad saat
`waifu run` mati.

### Dari terminal: `waifu jurnal`

```powershell
waifu jurnal                                       # hari yang ada catatannya
waifu jurnal show 2026-10-17                       # catatan satu hari, dengan jamnya
waifu jurnal add ketemu Rina di kampus             # tambah catatan hari ini
waifu jurnal add kemarin hujan --date 2026-10-16   # tambah catatan hari lain yang sudah lewat
```

```text
TANGGAL           CATATAN  ISI
Jumat 16-10-2026  1        kemarin hujan
Sabtu 17-10-2026  2        hari ini laporannya selesai dan aku main HSR sama…
```

Perintah ini jalan juga saat `journal.enabled: false` dan selagi `waifu run` berjalan (Cyrene
langsung melihat perubahannya); exit code 1 kalau filenya tidak bisa dibuka atau catatannya
ditolak.

`waifu check` menampilkan `✓ Jurnal: 12 hari tercatat, pertanyaan malam jam 21:30` (atau
`pertanyaan malam mati`) atau `! Jurnal mati`. File `data\journal.json` yang rusak dipindah ke
`journal.json.corrupt-<waktu>` (jurnal baru dimulai dari kosong), dan `check` menunjuknya sampai
Master memperbaiki atau menghapusnya.

### Pengaturan jurnal

```yaml
journal:
  enabled: true                 # false: tanpa tool dan tanpa pertanyaan malam (`waifu jurnal` tetap jalan)
  path: data/journal.json
  max_chars: 500                # panjang satu catatan paling banyak (20-2000)
  max_notes_per_day: 20         # catatan sehari paling banyak (1-100)
  ask_at: "21:30"               # kosong = tanpa pertanyaan malam (12:00-23:59)
  state_path: data/journal_question.json
```

Bagian ini tidak perlu ditulis untuk memakai bawaannya; contohnya ada di `config.example.yaml`.
Jalankan ulang `waifu run` setelah mengubahnya.

### Privasi jurnal

- `data\journal.json` hanya ada di PC ini (tidak di-commit) dan disimpan sampai Master menghapus
  harinya: tidak ada yang terhapus otomatis.
- Dengan Claude, teks yang dia tulis ke jurnal dan catatan yang dia baca (termasuk catatan dari
  `waifu jurnal add`) ikut ke API Anthropic dan tetap di riwayat obrolan sampai sesinya berakhir.
  Pertanyaan malam ikut membaca catatan hari itu. Dengan Ollama, semuanya tetap di PC.
- Dengan engine `edge`, jawaban yang menyebut isi jurnal dikirim ke edge-tts Microsoft untuk
  dijadikan suara, seperti semua jawaban.
- Jurnal tidak pernah dikirim sebagai notifikasi HP (`ntfy.sh`) dan tidak pernah disebut di
  sapaan pagi atau laporan mingguan; [rekap hari ini](#rekap-hari-ini) hanya menyebut jumlah
  catatannya. Tapi kalau Master menulis atau bertanya tentang jurnal lewat
  [Cyrene di HP](#cyrene-di-hp), jawabannya, termasuk isi jurnal yang dia baca, sampai ke HP itu
  lewat Tailscale seperti semua jawaban di HP.
- Log INFO mencatat panggilan `journal_write` dan `journal_read` tanpa isinya, tapi kalimat
  Master yang terdengar (`Heard ...`) tetap tercatat seperti biasa (lihat [Privasi](#privasi)).
- `data\journal_question.json` hanya menyimpan tanggal pertanyaan malam terakhir.

Untuk mematikannya, tulis `enabled: false` di bagian `journal`; catatan lama hilang kalau
`data\journal.json` dihapus.

## Rekap hari ini

"Hari ini aku ngapain aja?" dijawab sekali tanya dari catatan Cyrene sendiri di PC ini, tanpa
internet: tugas yang selesai dan yang masih menunggu, berapa lama Master fokus, berapa lama game
terbuka, pull gacha hari itu, kata Jepang yang dipelajari, jumlah catatan jurnal, jadwal hari
itu, dan berapa kali internet putus. Tidak ada pengaturan sendiri: tiap bagian ikut sakelar
fiturnya, bagian yang fiturnya mati dilewati, dan selama salah satunya menyala (daftar tugas,
teman fokus, jam main, jurnal, jadwal, dan pemantau internet sudah menyala dari awal) rekapnya ada
di `waifu run` dan `waifu chat`, dengan otak Ollama maupun Claude.

```text
Master : "Cyrene, hari ini aku ngapain aja?"
Cyrene : "Hari ini kamu menyelesaikan dua tugas, fokus lima puluh menit, dan dapat Acheron di
          HSR! Main game sekitar dua jam, dan tagihan listriknya masih menunggu ya."
Master : "Rekap kemarin dong."
Master : "Tanggal 20 aku ngapain?"
```

### Yang dia ceritakan

- **Tugas** dari [daftar tugas](#daftar-tugas): yang diselesaikan hari itu, dan yang masih
  terbuka dengan tenggat hari itu atau sebelumnya. Tugas selesai hanya diingat
  `todo.keep_done_days` hari (bawaan 7), jadi untuk hari yang lebih lama dia bilang tugas
  selesainya sudah tidak tersimpan, bukan nol, dan `waifu rekap` menulis
  `Tugas: yang selesai tidak tersimpan lagi (todo.keep_done_days: 7), ...`.
- **Fokus** dari [teman fokus](#teman-fokus): berapa lama Master fokus, di `waifu run` termasuk
  blok yang sedang jalan.
- **Jam main** dari [jam main](#jam-main): berapa lama tiap game terbuka; dua game yang terbuka
  bersamaan dihitung sekali di totalnya. Seperti biasa, hanya selama `waifu run` menyala, juga
  saat game-nya diminimalkan. Dia menyebutnya sebagai kabar saja, tidak pernah menilai terlalu
  lama atau menegur.
- **Pull gacha** dari riwayat pull yang tersimpan di `data\gacha\` (dengan
  [teman main gacha](#teman-main-gacha-hoyoverse) menyala): jumlah pull per game hari itu dan
  bintang 5 (S-rank di ZZZ) yang didapat. Riwayatnya tidak diperbarui di sini: pull sesudah
  riwayat terakhir dibaca (lewat pertanyaan riwayat gacha, laporan game mingguan, atau
  `waifu gacha history`) belum terhitung, dan HoYoverse mencatat pull sekitar satu jam kemudian.
- **Kata Jepang** dari [belajar bahasa Jepang](#belajar-bahasa-jepang), kalau menyala: kata baru
  hari itu (termasuk kata hari ini yang dia sebut di sapaan pagi) dan berapa kata yang diulang.
  Yang tersimpan hanya ulangan terakhir tiap kata, jadi untuk hari yang sudah lewat jumlah
  ulangannya bisa lebih kecil dari yang sebenarnya.
- **Jurnal** dari [jurnal harian](#jurnal-harian): hanya jumlah catatannya, tidak pernah
  isinya. Isinya dia baca kalau Master menanyakan jurnalnya.
- **Jadwal** dari [jadwal](#jadwal-pengingat-dan-rutinitas): pengingat dan rutinitas yang
  dijadwalkan hari itu, sudah lewat atau masih nanti. Cyrene tidak mencatat pengingat mana yang
  benar-benar diucapkan (pengingat hanya berbunyi selama `waifu run` dan PC menyala), dan
  pengingat sekali yang sudah lewat sudah hilang dari jadwal, jadi yang tersisa pengingat
  berulang dan yang masih nanti. Perintah perangkat terjadwal tidak disebut. Jadwal berulang
  yang diminta mulai belakangan ("tiap hari jam 8 mulai Jumat") tidak disebut sebelum mulainya,
  tapi hanya sampai waktu terakhir sebelum mulainya lewat: sesudah itu, dan sesudah pertama kali
  berbunyi, hari-hari sejak jadwal itu dibuat ikut tersebut, karena file jadwal tidak menyimpan
  tanggal mulainya.
- **Internet putus** dari [pemantau internet](#pemantau-internet): berapa kali dan berapa lama
  hari itu, hanya kalau pernah, dan hanya selama `waifu run` menyala. Riwayatnya disimpan
  `network.keep_days` hari (bawaan 7), jadi untuk hari yang lebih lama `waifu rekap` menulis
  `Internet putus: tidak tersimpan lagi (network.keep_days: 7)`.

Selain itu:

- **Harinya:** hari ini (sampai sekarang), kemarin, atau tanggal lain sampai 30 hari ke belakang.
  Untuk hari yang belum terjadi atau lebih jauh dari itu, dia bilang rekapnya tidak ada.
- **Tidak pernah soal uang:** pengeluaran dan saldo tetap dari
  [keuangan tepira](#keuangan-dari-tepira), kalau Master menanyakannya.
- **Hanya kalau Master bertanya:** sapaan pagi, laporan game mingguan, dan pertanyaan jurnal
  malam tidak memakainya.
- Kalau satu bagian tidak bisa dibaca (filenya terkunci, misalnya), dia bilang singkat bahwa
  bagian itu tidak tersedia dan menceritakan sisanya. Hari tanpa catatan apa pun dia sebut
  dengan hangat.
- Tugas dan jadwal memakai jam PC, bagian lain WIB; di PC yang jamnya WIB keduanya sama.

### Dari terminal: `waifu rekap`

`waifu rekap` menampilkan hari ini; `waifu rekap kemarin` atau `waifu rekap 2026-10-20` hari
lain, tanpa otak, dibaca sama seperti yang dia ceritakan:

```text
> waifu rekap
Rekap Sabtu 24-10-2026, sampai 21:30, dari catatan Cyrene:
Tugas: 2 selesai, 1 masih menunggu
  selesai: kirim laporan
  selesai: beli susu
  menunggu: bayar listrik, tenggat Sabtu 24-10-2026
Fokus: 50 menit
Main game: 1 jam 54 menit (Honkai: Star Rail 1 jam 9 menit, Genshin Impact 30 menit, Zenless Zone Zero 45 menit)
Gacha: Honkai: Star Rail 82 pull, 5★ Acheron (74 pull)
Jurnal: 2 catatan
Jadwal: 07:00 minum obat; 22:00 telepon Ibu (nanti)
Internet putus: 2 kali, total 16 menit
Hanya dari catatan di PC ini: jam main, fokus, dan internet putus dihitung selama 'waifu run' menyala, dan pull dari riwayat yang terakhir disimpan.
```

Bagian yang fiturnya mati tidak ditampilkan, dan bagian yang kosong menampilkan `tidak ada`.
Kalau satu bagian tidak bisa dibaca, baris `✗ ...` menyebutnya, sisanya tetap tampil, dan exit
code-nya 1. Kalau semua fiturnya mati, `waifu rekap` bilang rekapnya kosong. `waifu check` tidak
punya baris sendiri untuk rekap: baris tiap fiturnya sudah cukup.

### Privasi rekap

- Rekap ditutup selama [mode tamu](#mode-tamu): isinya tugas, game, pull, dan teks pengingat
  Master.
- Rekap hanya membaca file yang sudah ada di PC ini dan tidak menyimpan apa pun; tidak ada yang
  dikirim ke internet karenanya, juga tidak ke HoYoLAB.
- Dengan Claude, hasilnya (teks tugas, nama game, pull, kata Jepang, teks pengingat, dan
  angka-angkanya, tanpa isi jurnal dan tanpa uang) ikut ke API Anthropic seperti obrolan biasa;
  dengan Ollama hasilnya tidak keluar dari PC (jawaban yang menyebutnya tetap dijadikan suara oleh
  edge-tts kalau engine `edge` dipakai, seperti jawaban lainnya). Rekap tidak pernah dikirim
  sebagai notifikasi HP, tapi kalau Master bertanya lewat [Cyrene di HP](#cyrene-di-hp),
  jawabannya sampai ke HP itu seperti semua jawaban di HP.

## Pencarian web dan promo gratis

Dengan otak Claude, Cyrene bisa mencari di web untuk pertanyaan yang jawabannya baru atau berubah,
lalu menjawab singkat dan menyebut sumbernya. Dia juga memberi tahu **game yang gratis untuk
disimpan** (Epic Games Store, Steam, GOG, dan lainnya) dan **promo AI** dari penyedia ternama
(misalnya Claude for Startups dari Anthropic, kredit atau paket gratis dari OpenAI, Google Gemini,
dan lainnya) yang bisa langsung diklaim gratis atau perlu mendaftar. Selama `waifu run` dia bilang
sendiri saat ada yang baru, dan mengirim linknya ke HP kalau notifikasi menyala.

**Dia hanya memberi tahu.** Cyrene tidak pernah mengklaim game, mendaftar program, login ke akun,
atau membeli apa pun untuk Master, tidak pernah membuka link promo di PC, dan tidak pernah
menyarankan membayar. Mengklaim dan mendaftar selalu dilakukan Master sendiri.

**Setelah memperbarui ke versi ini, jalankan ulang `waifu run` dulu** sebelum memakai
`waifu chat` dengan Claude atau `waifu offers ai --scan`. `waifu run` versi lama yang masih
berjalan menganggap `data\usage.json` yang memuat jumlah pencarian web sebagai file rusak dan
memindahkannya (lihat [File data\usage.json](#file-datausagejson)).

```text
Master : "Cyrene, cari berita terbaru soal RTX 5070."
Master : "Cyrene, harga RTX 5070 sekarang berapa?"
Master : "Cyrene, kapan HSR 4.7 rilis?"
Master : "Cyrene, ada game gratis apa sekarang?"
Cyrene : "Ada Bad Cheese di Epic sampai Kamis jam sepuluh malam, dan Portal di Steam sampai
          25 Oktober, infonya dari GamerPower.com."
Master : "Kirim linknya Bad Cheese ke HP."        (tanpa memanggil nama, masih dalam jendela lanjutan)
Master : "Cyrene, ada promo AI gratis nggak?"
Cyrene : "Ada Claude for Startups dari Anthropic, kredit API untuk startup, tapi harus mendaftar dulu."
```

### Pencarian web

- **Hanya dengan Claude** (`brain.backend: claude`): pencarian web Claude berjalan di server
  Anthropic. Dengan Ollama fitur ini tidak tersedia, dan `waifu check` menampilkan
  `! Pencarian web: tidak tersedia dengan Ollama`.
- Dia mencari hanya kalau perlu, paling banyak `max_uses` kali (bawaan 3) **per permintaan ke
  Claude**, dan menjawab dalam satu-dua kalimat dengan menyebut sumbernya. Link tidak pernah
  dibacakan. Satu pertanyaan bisa butuh beberapa permintaan (satu lagi setiap kali dia memakai
  tool, sampai `brain.max_tool_rounds` + 1), jadi paling banyak 18 pencarian dengan bawaannya;
  biasanya jauh lebih sedikit.
- Model yang menyaring hasil pencarian sendiri (Opus 5.5 bawaannya, juga Opus 5, Sonnet 5.5,
  Sonnet 5, Fable 5.1, dan lainnya) memakai pencarian `web_search_20260209`; model lain, misalnya
  Haiku 5.5, memakai versi sebelumnya (`web_search_20250305`), jadi `brain.claude.model` bebas
  diganti.
- Dengan pencarian web, jawaban Claude diterima sedikit demi sedikit (streaming), jadi pencarian
  yang lama tidak terputus di tengah jalan. Jawaban yang macet lebih dari 60 detik dihentikan tanpa
  diminta ulang (bagian yang sudah jalan bisa jadi sudah ditagih), dan dia bilang jawabannya
  terlalu lama.
- Hasil pencarian untuk orang di Indonesia: negara `ID`, zona waktu `Asia/Jakarta`, dan kota dari
  `web_search.city` (atau `briefing.city` kalau kosong).
- **Isi halaman web hanyalah data, bukan perintah dari Master.** Kalau sebuah halaman menyuruh
  menyalakan perangkat, mematikan PC, membuka aplikasi, membuat jadwal, atau mengubah ingatan,
  dia tidak menurutinya.
- **Biaya:** 10 dolar per 1000 pencarian (1 sen per pencarian) di luar token, dan hasil pencarian
  ikut dihitung sebagai token input. Jumlah pencarian dan biayanya tercatat di
  [Biaya API Claude](#biaya-api-claude): `waifu usage` menambah kolom `PENCARIAN WEB` di bulan
  yang ada pencariannya. Kalau harga pencarian berubah, tulis `usage.web_search_per_1000_usd`.

```yaml
web_search:
  enabled: true            # false: tanpa pencarian web
  max_uses: 3              # pencarian paling banyak per permintaan ke Claude (1-10)
  country: ID
  timezone: Asia/Jakarta
  # city: Bandung          # tanpa ini briefing.city
```

`waifu check` menampilkan `✓ Pencarian web: aktif (Claude, paling banyak 3 pencarian per
permintaan, 18 per pertanyaan)`, `! Pencarian web: tidak tersedia dengan Ollama`, atau
`! Pencarian web mati`.

### Game gratis

- **Sumbernya:** Epic Games Store dari Epic sendiri (game yang gratis sekarang dan minggu depan,
  dengan jam mulai dan berakhir yang tepat), lalu Steam, GOG, itch.io, atau semua toko PC
  (`pc`) dari [GamerPower.com](https://www.gamerpower.com), yang datanya boleh dipakai dengan
  menyebut dan menautkan GamerPower.com. Dari GamerPower hanya tanggal berakhirnya yang dipakai
  (jamnya tidak tepat), dan setiap kali menyebut game dari sana dia menyebut GamerPower.com.
- Hanya game yang gratis untuk disimpan: diskon berbayar, DLC, item, dan beta tidak dihitung.
- Saat ditanya, daftar gamenya dibaca paling lama 30 menit sebelumnya.
- **Pemberitahuan:** setiap `games_poll_hours` (bawaan 6 jam) selama `waifu run`, mulai begitu
  dia siap mendengarkan, game gratis yang belum pernah diberitahukan disebut dalam satu kalimat
  dengan wajah senang, misalnya "Master, ada game gratis baru: Bad Cheese dan Lost Castle di
  Epic, sampai Kamis 22.00." Kalau ada game dari GamerPower, dia menutupnya dengan "infonya dari
  GamerPower.com", misalnya "Master, ada game gratis baru: Portal di Steam, sampai 25 Oktober,
  infonya dari GamerPower.com." Paling banyak tiga judul disebut, sisanya dihitung. Game yang baru
  gratis minggu depan diberitahukan saat sudah bisa diklaim. Setiap game hanya sekali; game yang
  diberikan gratis lagi lebih dari 30 hari setelah berakhir dianggap baru lagi.

### Promo AI

- **Pencarian mingguan:** selama pemberitahuan menyala (`notices: true`), setiap `ai_scan_days`
  (bawaan 7 hari), dan pertama kali begitu `waifu run` siap, Claude mencari di web promo yang
  sedang dibuka penyedia AI ternama
  (Anthropic, OpenAI, Google Gemini, xAI, Mistral, Meta, AWS, Microsoft, GitHub Copilot, Hugging
  Face, dan lainnya): kredit gratis, paket gratis, uji coba, serta program startup, riset, atau
  pelajar, yang bisa didapat dari Indonesia. Setiap promo dicatat dengan penyedianya, jenisnya,
  apakah **bisa langsung diklaim** atau **perlu mendaftar**, siapa yang bisa mendapatkannya,
  batas waktunya kalau ada, dan link ke halaman penyedianya sendiri. Skema referral, giveaway yang
  butuh pembayaran, dan promo yang sudah berakhir tidak dihitung. Dengan `notices: false`
  pencarian mingguan ini juga berhenti: jalankan `waifu offers ai --scan` sendiri.
- Pencarian ini memakai Claude (`brain.claude.model`) **juga dengan otak Ollama**, jadi butuh API
  key Claude; tanpa itu, tulis `ai: false`. Pencarian ini hanya punya pencarian web dan satu tool
  untuk melaporkan hasilnya: tidak ada perangkat, PC, ingatan, atau jadwal di dekatnya, dan yang
  dikirim tentang Master hanya negaranya (Indonesia).
- **Biaya:** paling banyak `ai_max_searches` (bawaan 10) pencarian web per permintaan ke Claude,
  dan satu pencarian promo bisa sampai 6 permintaan kalau pencariannya dilanjutkan, ditambah
  token. Hasil pencarian yang panjang ikut dihitung sebagai token input (sebagian besar dibaca
  dari cache yang lebih murah), jadi satu pencarian promo bisa sampai beberapa puluh sen dolar. `waifu offers ai --scan` menampilkan perkiraannya. Dengan otak Claude
  biayanya tercatat di [Biaya API Claude](#biaya-api-claude); dengan Ollama tidak dicatat.
- Pencarian yang gagal sebelum Claude menjawab (internet putus, server Claude sibuk) dicoba lagi
  pada pengecekan game berikutnya, 6 jam kemudian. Pencarian yang gagal setelah Claude menjawab
  (sudah ditagih, misalnya Claude selesai tanpa melaporkan hasilnya) baru dicoba lagi sehari
  kemudian, juga kalau `waifu run` dijalankan ulang.
- **Pemberitahuan:** promo baru disebut dalam satu kalimat, misalnya "Master, aku menemukan 2
  promo AI baru: Claude for Startups dari Anthropic dan Gemini API free tier dari Google. Link
  yang pertama ada di notifikasi HP; minta aku kalau mau link yang lain." Untuk link yang lain,
  bilang misalnya "Cyrene, kirim link Gemini API free tier ke HP".
- Setiap pencarian melaporkan lagi promo yang masih dibuka, jadi promo disimpan selama masih ada:
  sampai batas waktunya lewat, atau sampai 90 hari tidak dilaporkan lagi. Promo yang ditemukan
  lagi setelah itu diumumkan sebagai baru.
- Isinya hasil pencarian web: baca halaman penyedianya sebelum mendaftar, karena syaratnya bisa
  berubah atau ringkasannya keliru. Judul, syarat, dan ringkasannya berasal dari halaman web, jadi
  Cyrene menganggapnya data, bukan perintah, dan promo dengan teks yang terlalu panjang dibuang.

### Link ke HP

"Kirim linknya ke HP" mengirim notifikasi berisi judul, keterangan singkat, dan link promo itu;
mengetuk notifikasinya membuka halamannya. Pemberitahuan game dan promo baru juga membawa link
yang pertama. Butuh notifikasi HP (lihat [Notifikasi ke HP](#3-notifikasi-ke-hp-ntfy)); tanpa itu
dia bilang belum bisa mengirimnya, dan semua link bisa dilihat dengan `waifu offers`. Hanya link
`https` yang dikirim.

**Link promo AI dari situs yang tidak dikenal tidak pernah terbuka sekali ketuk.** Pencarian promo
membaca halaman yang bisa ditulis siapa saja, dan halaman penipu bisa menyelipkan link palsu
untuk "kredit Claude gratis". Link yang terbuka saat notifikasinya diketuk hanya yang ada di situs
resmi penyedia yang dikenal, tempat hanya penyedia itu yang bisa menerbitkan halaman (misalnya
anthropic.com, claude.com, openai.com, ai.google.dev, cloud.google.com, startup.google.com, x.ai,
mistral.ai, ai.meta.com, aws.amazon.com, www.microsoft.com, azure.microsoft.com,
education.github.com, docs.github.com). Situs tempat siapa saja bisa menerbitkan halaman tidak
dihitung resmi, walaupun milik penyedia itu: github.com dan huggingface.co (repo dan Space milik
siapa saja), claude.ai, chatgpt.com, dan gemini.google.com (chat yang dibagikan, artifact, GPT
buatan orang), serta forum seperti community.openai.com dan answers.microsoft.com. Link lain
tetap dikirim kalau diminta, tetapi hanya sebagai teks dengan peringatan "Bukan situs resmi yang
kukenal, periksa alamatnya dulu", dan pemberitahuan promo barunya tidak membuka apa-apa. Link
yang alamat situsnya bisa dibaca lain oleh browser, yaitu yang memakai nama pengguna, port,
garis miring terbalik (`\`), spasi, atau karakter tak terlihat (misalnya
`https://anthropic.com@situs-lain.example/`), dibuang sejak pencarian dan tidak pernah terbuka
sekali ketuk.

### Melihat promo: `waifu offers`

`waifu offers` menampilkan game gratis lalu promo AI, `waifu offers games` gamenya saja, dan
`waifu offers ai` promo AI yang tersimpan, masing-masing dengan linknya:

```text
Game gratis sekarang:
  Bad Cheese di Epic Games Store, sampai Kamis 22-10-2026 22:00
    https://store.epicgames.com/en-US/p/bad-cheese-4256d9
  Portal di Steam (biasanya $9.99), sampai Minggu 25-10-2026
    https://www.gamerpower.com/open/portal-steam-giveaway
Segera gratis:
  Sker Ritual di Epic Games Store, mulai Kamis 22-10-2026 22:00, sampai Kamis 29-10-2026 22:00
    https://store.epicgames.com/en-US/p/sker-ritual-aac702
Sumber: GamerPower.com (https://www.gamerpower.com)

Promo AI (pencarian terakhir Rabu 14-10-2026 09:30):
  Claude for Startups dari Anthropic: program startup, perlu mendaftar
    Kredit API Claude gratis untuk startup.
    Untuk: Startup yang didukung modal ventura mitra Anthropic.
    https://www.anthropic.com/startups
```

Link promo AI yang bukan dari situs resmi penyedia yang dikenal ditandai
`(belum terverifikasi: bukan situs resmi penyedia yang dikenal)`.

`waifu offers ai --scan` menjalankan pencarian promo AI sekarang juga (bisa beberapa menit),
menyimpan hasilnya, lalu menampilkan promo yang baru dan perkiraan biayanya, misalnya
`Perkiraan biaya pencarian ini: $0.18 (1 permintaan, 6 pencarian web).` Perintah ini jalan
walau `free_offers.enabled: false`, juga selagi `waifu run` berjalan; promo yang ditemukan
`--scan` tidak diumumkan lagi oleh `waifu run`. Hanya satu pencarian promo AI yang berjalan
sekaligus: kalau `waifu run` sedang mencari, `--scan` langsung berhenti tanpa biaya dengan
`✗ Pencarian promo AI lain sedang berjalan, misalnya oleh 'waifu run'. Coba lagi setelah
selesai.`, dan sebaliknya `waifu run` mencoba lagi pada pengecekan berikutnya.

`waifu check` menampilkan misalnya `✓ Promo gratis: game Epic, Steam, GOG; promo AI terakhir
dicari Rabu 14-10-2026 09:30` (ditambah `tanpa pemberitahuan dan tanpa pencarian promo AI
mingguan` dengan `notices: false`), atau `! Promo gratis mati`; internet tidak dihubungi.

### Pengaturan promo gratis

```yaml
free_offers:
  enabled: true                       # false: tanpa tool dan tanpa pemberitahuan (`waifu offers` tetap jalan)
  games: true                         # false: tanpa game gratis
  game_platforms: [epic, steam, gog]  # dari epic, steam, gog, itchio, pc (semua toko PC)
  games_poll_hours: 6                 # cek game gratis tiap N jam selama `waifu run` (1-48)
  ai: true                            # false: tanpa pencarian promo AI
  ai_scan_days: 7                     # cari promo AI tiap N hari (1-30)
  ai_max_searches: 10                 # pencarian web paling banyak per permintaan ke Claude dalam pencarian promo AI (1-20)
  notices: true                       # false: tidak pernah bilang sendiri, dan pencarian promo AI mingguan juga berhenti
  state_path: data/free_offers.json
```

Bagian ini tidak perlu ditulis untuk memakai bawaannya; contohnya ada di `config.example.yaml`.
Jalankan ulang `waifu run` setelah mengubahnya.

### Privasi pencarian dan promo

- Pencarian web hanya mengirim kata kunci yang ditulis modelnya, ditambah negara, zona waktu,
  dan kota di atas, ke pencarian Anthropic. Halaman yang dibacanya tidak disimpan.
- Daftar game diminta dari Epic dan GamerPower.com tanpa data tentang Master (Epic hanya diberi
  tahu negaranya, Indonesia).
- Pencarian promo AI tidak mengirim data pribadi apa pun selain negara (Indonesia) dan judul
  promo yang sudah tersimpan.
- `data\free_offers.json` hanya ada di PC ini dan tidak di-commit: id game yang sudah
  diberitahukan, promo AI yang ditemukan, dan waktu pencarian terakhir (juga yang terakhir gagal
  setelah ditagih). Seperti file Cyrene
  lainnya, file yang rusak tidak pernah ditimpa: namanya diganti menjadi
  `free_offers.json.corrupt-<tanggal>-<jam>`, dia mulai dari kosong (game yang sedang gratis
  diberitahukan sekali lagi), dan `waifu check` menunjukkannya dengan `!`.
- Teks pemberitahuan dan link yang dikirim ke HP lewat `ntfy.sh` kalau notifikasi HP menyala.

## Keuangan dari tepira

Cyrene bisa menjawab pertanyaan soal uangmu dari **tepira** (https://tepira.asepsunarya.com,
aplikasi keuanganmu sendiri) dan, kalau kamu mau, mencatat pengeluaran lewat suara. Dia membaca
tepira lewat API khusus Cyrene (`/api/cyrene/*`) yang hanya melayani pemiliknya dengan token
sendiri; Cyrene tidak pernah memegang akses database tepira. Fitur ini **mati dari awal**
(`tepira.enabled: false`): API-nya baru ada setelah PR #21 tepira di-merge dan di-deploy, dan
sebelum itu tepira menjawab 404, yang Cyrene sampaikan sebagai "Akses Cyrene di tepira belum
aktif."

**Hanya kalau Master bertanya.** tepira tidak pernah dibaca sendiri: tidak di sapaan pagi, tidak
di pemberitahuan atau notifikasi HP, dan angkanya tidak disimpan dengan `remember` kecuali Master
minta. Sapaan pagi selalu dimulai di sesi baru, jadi angka dari pertanyaan beberapa menit
sebelumnya tidak ikut ke sapaan atau notifikasinya.

```text
Master : "Cyrene, pengeluaran bulan ini berapa?"
Cyrene : "Dua koma tiga lima juta dari empat puluh satu transaksi, Master. Hari ini baru empat puluh lima ribu."
Master : "Sisa budget makan?"
Cyrene : "Makan harian sisa dua ratus dua puluh ribu dari satu koma dua juta."
Master : "Catat jajan kopi 18 ribu."
Cyrene : "Baik, Master. Catat delapan belas ribu untuk jajan kopi di GoPay, Master? Jawab iya kalau jadi."
Master : "Iya."                                 (ucapan berikutnya, tanpa memanggil nama)
Cyrene : "Sudah kucatat di GoPay, Master."
Master : "Cyrene, batalkan yang tadi."
Cyrene : "Sudah kubatalkan, saldo GoPay kembali."
```

### Yang bisa dia jawab

- **Pengeluaran dan pemasukan bulan ini:** "pengeluaran bulan ini berapa?", "hari ini habis
  berapa?", "pemasukan bulan ini berapa?" Total pengeluaran (dari gaji dan dari bonus, hari ini,
  kemarin, minggu ini) dan pemasukan (gaji, bonus, saldo awal, sisa, dan perkiraan saldo akhir
  seperti di dashboard). "Bulan lalu" juga bisa.
- **Budget per kategori:** "sisa budget makan?" Alokasi, terpakai, sisa, dan persennya. Basis
  budget-nya gaji plus bonus, seperti halaman Laporan tepira; dia menyebutnya kalau budget-nya
  lewat.
- **Saldo dompet dan kekayaan bersih:** "saldo GoPay berapa?", "kekayaan bersihku berapa?"
  Dompet utang (kartu kredit, paylater) bersaldo negatif, dan kekayaan bersih adalah aset
  dikurangi kewajiban.
- **Tujuan tabungan:** "tabungan laptop sudah berapa?" Terkumpul, target, persen, kurangnya,
  tenggat, dan perlu berapa per bulan.
- **Transaksi terakhir:** "transaksi terakhir apa?", "jajan terakhir apa aja?" (paling banyak 10).
- **Skor keuangan mingguan:** "skor keuanganku gimana?"

Batas bulan mengikuti tampilan web tepira (bulan server): pengeluaran jam 00.00–06.59 WIB tanggal
1 masih terhitung bulan sebelumnya, sama seperti di dashboard. "Hari ini", "kemarin", dan "minggu
ini" memakai WIB. Dia tidak menebak angka yang tidak ada di jawaban tepira, dan menyebut angkanya
dibulatkan.

Yang belum bisa: menabung ke tujuan, mengubah pengeluaran, mencatat pemasukan atau transfer,
mencatat untuk hari lain atau dari bonus, dan menghapus pengeluaran selain catatannya sendiri
yang terakhir. Semua itu lewat tepira langsung.

### Mencatat pengeluaran lewat suara

Hanya dengan `tepira.writes: true`, otak Claude (dengan Ollama dia hanya membaca), dan saklar
tulis tepira menyala (`TEPIRA_CYRENE_WRITES=1` di Vercel).

- "Catat jajan kopi 18 ribu" membuatnya **bertanya dulu**: "Catat delapan belas ribu untuk jajan
  kopi di GoPay, Master? Jawab iya kalau jadi." Pertanyaan ini dibuat kode Cyrene sendiri, bukan
  modelnya, dan menyebut nominal, untuk apa, dan dompetnya, supaya salah dengar ("18 ribu" jadi
  "80 ribu") ketahuan sebelum tercatat.
- Pengeluarannya baru tercatat kalau **ucapan Master berikutnya** adalah "iya" (juga "iya, catat
  aja", "boleh", "oke"), dalam 60 detik. Diam, "jangan", kalimat lain, atau "iya" yang datang
  lebih lambat: tidak ada yang tercatat. Hanya satu pertanyaan seperti ini per giliran, juga
  bersama konfirmasi mematikan PC.
- Dompetnya `tepira.default_wallet`, kecuali Master menyebut dompet lain: "catat bensin 30 ribu
  pakai BCA". Namanya harus sama dengan di tepira (huruf besar dan kecil tidak dibedakan); kalau
  tidak ada, dia menyebut dompet yang ada.
- Tercatat dengan waktu sekarang, sumber gaji, paling banyak Rp5.000.000 per catatan; kategori
  hanya kalau jelas cocok dengan salah satu dari 9 kategori tepira. Keterangan yang terlihat
  seperti rahasia (PIN, kata sandi, nomor kartu) tidak dicatat.
- **Membatalkan:** "batalkan yang tadi" menghapus pengeluaran yang terakhir dia catat, paling
  lama **15 menit** setelah dicatat (batas dari tepira), dan saldo dompetnya kembali. Hanya
  catatannya sendiri yang terakhir, dan hanya selama `waifu run` atau `waifu chat` yang
  mencatatnya masih berjalan; selebihnya hapus di tepira.
- Kalau tepira tidak menjawab dengan jelas saat mencatat, dia bilang belum tahu apakah sudah
  tercatat. Mencatat yang sama lagi dalam dua menit tidak membuat catatan ganda: tepira
  mengembalikan catatan yang sudah ada.

### Menyiapkan

1. **PR #21 tepira di-merge dan di-deploy** (API `/api/cyrene/*`). Sebelum itu setiap
   permintaan dijawab 404: "Akses Cyrene di tepira belum aktif."
2. **Buat token di PC:** `waifu tepira token` (tanpa internet, tanpa config). Hasilnya dua baris,
   `token : tpcy_...` dan `sha256: ...`. Token itu rahasia seperti kata sandi: jangan dibagikan
   atau ditempel di chat. Baris sha256 bukan rahasia; simpan juga, karena rotasi token nanti
   butuh hash lama.
3. **Vercel → Settings → Environment Variables** (scope Production, tandai *Sensitive*), lalu
   redeploy:
   - `TEPIRA_CYRENE_TOKEN_SHA256` = baris sha256 tadi;
   - `TEPIRA_CYRENE_USER_ID` = `id` milikmu di tabel `User` (Supabase Table Editor; salin dari
     selnya, jangan diketik ulang);
   - `TEPIRA_CYRENE_WRITES` = `1`, hanya kalau mau mencatat lewat suara (hapus variabelnya lalu
     redeploy untuk mematikannya lagi).
4. **Bagian `tepira` di `config.yaml`** (contohnya ada di `config.example.yaml`):

   ```yaml
   tepira:
     enabled: true
     url: https://tepira.asepsunarya.com
     token: tpcy_...           # baris token dari langkah 2
     writes: false             # true: catat lewat suara (otak Claude dan TEPIRA_CYRENE_WRITES=1)
     default_wallet: GoPay     # wajib kalau writes: true; nama dompet seperti di tepira
   ```

5. **`waifu check`** menampilkan `✓ Keuangan tepira: tepira.asepsunarya.com, token ada; catat
   transaksi mati; uji dengan: waifu tepira`; dengan writes dan Claude `catat transaksi aktif ke
   dompet GoPay`, dengan writes dan Ollama `!` dan `catat transaksi tidak tersedia dengan
   Ollama`. Baris ini tidak menghubungi tepira dan tidak pernah menampilkan tokennya.
6. **`waifu tepira`** menampilkan ringkasan bulan ini; cocokkan dengan dashboard tepira.
   `✗ Akses Cyrene di tepira belum aktif.` berarti PR #21 belum di-deploy atau env Vercel-nya
   kosong atau salah; `✗ Token tepira ditolak...` berarti hash di Vercel tidak cocok dengan token
   di `config.yaml`.
7. **Mulai ulang `waifu run`** supaya tool keuangannya aktif.

Yang bisa bertanya soal uang adalah siapa pun yang lolos verifikasi suara dan HP yang sudah
dipasangkan. Kalau ada tamu, bilang "Cyrene, mode tamu" (lihat [Mode tamu](#mode-tamu)):
tool keuangan ditolak sampai mode tamu selesai.

### Dari terminal: `waifu tepira`

```powershell
waifu tepira                  # ringkasan bulan ini (sama dengan: waifu tepira ringkasan)
waifu tepira dompet           # saldo tiap dompet, lalu aset, kewajiban, dan kekayaan bersih
waifu tepira tujuan laptop    # tujuan tabungan yang namanya memuat "laptop" (tanpa nama: semua)
waifu tepira transaksi -n 10  # transaksi terbaru, 1 sampai 10 (bawaan 5)
waifu tepira token            # token API baru dan hash-nya, sekali tampil
```

```text
Keuangan Oktober 2026 (per 2026-10-16 09:12 WIB)
Pengeluaran: Rp2.350.000 dari 41 transaksi (gaji Rp2.100.000, bonus Rp250.000); hari ini Rp45.000, kemarin Rp120.000, minggu ini Rp310.000
Pemasukan: gaji Rp5.000.000, bonus Rp500.000, saldo awal Rp1.000.000; sisa Rp3.150.000, perkiraan saldo akhir Rp4.150.000
Budget (gaji+bonus): Rp3.000.000, terpakai Rp2.350.000 (78,3%), sisa Rp650.000
- Makan harian: sisa Rp220.000 dari Rp1.200.000 (81,7%)
- Nongkrong / jajan: lewat Rp42.000 dari Rp300.000 (114%)
Tanpa kategori Rp35.000; di luar budget Rp0
Skor mingguan: 72 (SEHAT), minggu mulai 2026-10-12
```

Perintah ini membaca tepira langsung (tanpa cache), sama dengan yang dibaca Cyrene, jalan walau
`writes: false`, dan tidak pernah mencatat apa pun. Exit code-nya 2 kalau `tepira.enabled:
false`, 1 kalau tepira tidak menjawab (pesannya diawali `✗`), dan 0 kalau berhasil.

### Privasi keuangan

- tepira hanya dibaca saat Master bertanya, tidak pernah dicek berkala.
- Dengan otak Claude, hasil tool-nya (angka, nama dompet dan tujuan, catatan transaksi) ikut ke
  API Anthropic bersama obrolan dan tetap ada di riwayat sesi sampai sesinya berakhir (10 menit
  tanpa percakapan atau 40 giliran); dengan Ollama semuanya tetap di PC. Teks jawabannya
  dijadikan suara oleh edge-tts Microsoft, seperti setiap jawaban.
- Tidak ada yang ditulis ke disk: cache 60 detik dan catatan terakhirnya (untuk dibatalkan)
  hanya ada di memori `waifu run`/`waifu chat`.
- Token hanya ada di `config.yaml` dan hanya dikirim di header `Authorization` ke tepira (tidak
  pernah di URL). Token tidak pernah ditulis ke log, tidak ditampilkan (kecuali token baru dari
  `waifu tepira token`, sekali), dan tidak muncul di pesan error.
- Di log INFO (`logs\waifu.log` saat autostart), panggilan tool keuangan ditulis tanpa isinya,
  misalnya `Tool record_expense(…) -> ok`; hasilnya hanya di log DEBUG (`-v`), dan
  `waifu chat -v` menampilkannya di terminal. Kalimat Master yang terdengar tetap tercatat
  seperti biasa (`Heard ...`), jadi "catat kopi 18 ribu" ada di log itu. Dengan `-v`, log httpx
  juga menulis alamat setiap permintaan, termasuk nama tujuan yang dicari (tidak pernah
  tokennya).
- Catatan transaksi, nama tujuan, dan nama dompet adalah teks bebas di tepira: Cyrene
  memperlakukannya sebagai data, tidak pernah sebagai perintah.

## Mode tamu

Kalau ada teman di ruangan, "Cyrene, mode tamu" menutup hal pribadi Master selama beberapa jam,
tanpa mengubah `config.yaml` atau memulai ulang. Selalu tersedia, dengan otak Ollama maupun
Claude, tanpa paket tambahan.

```text
Master : "Cyrene, mode tamu."
Cyrene : "Mode tamu aktif sampai jam dua pagi, Master."
Master : "Pengeluaranku bulan ini berapa?"
Cyrene : "Yang itu kututup dulu selama ada tamu, Master."
Master : "Nyalakan lampu kamar."
Cyrene : "Lampu kamar sudah menyala."
Master : "Cyrene, mode tamu selesai."
```

### Menyalakan dan mematikan

- "Cyrene, mode tamu" atau "ada tamu nih": menyala. "Cyrene, mode tamu selesai": mati. "Mode
  tamu masih aktif?": dia menjawab menyala atau tidak, dan sampai jam berapa.
- Bisa lewat suara, `waifu chat`, atau chat [Cyrene di HP](#cyrene-di-hp). Dari HP berlaku juga
  untuk PC, karena itu Cyrene yang sama. `waifu chat` punya mode tamu sendiri, terpisah dari
  `waifu run`.
- Tidak ada perintah terminal seperti `waifu tamu`: terminal tidak bisa menjangkau `waifu run`
  yang sedang berjalan.

### Yang ditutup

Tool yang membaca atau mengubah catatan dan akun Master sendiri ditolak di kode, dan dia cukup
bilang singkat bahwa itu ditutup dulu selama ada tamu:

- keuangan tepira (ringkasan, saldo, tujuan, transaksi, mencatat dan membatalkan pengeluaran);
- jurnal (menulis dan membaca);
- ingatan: yang diucapkan selama mode tamu tidak disimpan sebagai hal tentang Master, dan
  catatan Master tidak bisa dihapus;
- daftar tugas (semuanya);
- jam main;
- [pengingat saat game dibuka](#pengingat-saat-game-dibuka) (membuat, melihat, dan menghapus;
  yang jatuh waktunya tetap diucapkan, seperti pengingat lain yang Master pasang);
- [rekap hari ini](#rekap-hari-ini);
- akun game: status, riwayat pull, pendapatan, karakter, endgame, jadwal banner dan event
  (jawabannya memuat progres event dan bintang endgame Master), laporan mingguan, dan kode
  redeem;
- daftar jadwal dan membatalkan jadwal (isinya teks pengingat Master), juga daftar alarm dan
  membatalkan alarm;
- biaya API Claude;
- melihat layar PC;
- membaca clipboard PC ([Artinya apa](#artinya-apa)), juga lewat Ctrl+Alt+T.

### Yang tetap jalan

- Perangkat rumah (mematikan PC tetap dengan konfirmasi), rutinitas, menambah jadwal atau
  pengingat, lagu di YouTube, [volume suaranya](#volume-suaraku), kesehatan PC, cuaca dan
  ringkasan hari (tanpa tugas dan teks pengingat), [cuaca beberapa hari](#cuaca-beberapa-hari),
  promo gratis, pencarian web, dan obrolan biasa.
- [Teman fokus](#teman-fokus): memulai, menghentikan, dan menanyakan sisa waktunya, jadi sesi
  yang jalan sebelum tamu datang tetap bisa dihentikan. Jawabannya bisa menyebut berapa lama
  Master fokus hari itu dan minggu itu.
- [Belajar bahasa Jepang](#belajar-bahasa-jepang): kata hari ini, kuis, dan kemajuannya. Kata
  pemula dan berapa kata yang Master hafal bukan hal pribadi. Hanya menyimpan kata baru ("simpan
  kata itu") yang ditolak: kata tamu tidak masuk ke latihan Master.
- [Pengingat istirahat](#pengingat-istirahat): kalimatnya tidak menyebut hal pribadi.
- [Pemantau internet](#pemantau-internet): jawabannya, kalimat saat internet nyambung lagi, dan
  tool `network_status`. Kondisi internet bukan hal pribadi, dan alamat tidak pernah disebut.
- [Waktu sholat](#waktu-sholat): jadwalnya dan pengingatnya. Waktu sholat untuk kota yang sudah
  disebut cuaca bukan hal pribadi, dan koordinat rumah tidak pernah dia sebut.
- [Alarm bangun](#alarm-bangun): alarm tetap berbunyi, dan memasang, mematikan, atau menunda alarm
  tetap bisa.
- [Tombol cepat di HP](#tombol-cepat-di-hp) dan [di PC](#tombol-cepat-di-pc): tombolnya
  mengendalikan perangkat tanpa otak Cyrene.
- Perintah terminal (`waifu todo`, `waifu jurnal`, `waifu tepira`, dan lainnya): terminal milik
  Master.

### Obrolan baru tanpa ingatan

Mulai giliran sesudah mode tamu dinyalakan, Cyrene memulai obrolan baru: obrolan sebelumnya (yang
mungkin berisi jawaban soal uang atau jurnal) dan [ingatannya](#ingatan-cyrene) tentang Master
tidak ikut. Begitu juga saat mode tamu selesai: obrolan baru dimulai, dan ingatannya kembali.
Pertanyaan konfirmasi yang belum dijawab (misalnya mematikan PC) ikut terlupa, jadi "iya"
sesudahnya tidak menjalankan apa pun. Giliran yang menyalakan mode tamu masih memakai obrolan
lama; persona memintanya tidak menyebut hal pribadi Master di jawaban itu.

### Sapaan dan pertanyaan yang dia mulai sendiri

- [Sapaan pagi](#sapaan-pagi-dan-ringkasan-harian) tetap diucapkan, tapi hanya cuaca dan jadwal
  perangkat hari ini, tanpa tugas, teks pengingat, game, atau ingatan (begitu juga notifikasi
  HP-nya kalau `briefing.push` menyala).
- Pertanyaan malam [jurnal](#jurnal-harian) dan [laporan game mingguan](#laporan-game-mingguan)
  dilewati selama mode tamu, dan tidak diulang hari itu (atau minggu itu). `waifu gacha report`
  tetap menampilkan minggunya.
- Yang tetap diucapkan apa adanya: pengingat dan jadwal yang Master pasang sendiri, dengan
  teksnya (Master sendiri yang memilih isinya), kalimat teman fokus, pengingat waktu sholat, dan
  pemberitahuan game, kode redeem, PC, hujan, versi baru, promo, biaya API (menyebut pengeluaran
  API), dan pengingat tidur. Pengingat yang terlalu pribadi bisa dibatalkan dulu dengan
  `waifu schedule cancel <id>`.

### Kapan berakhir

- Sendiri setelah `guest_mode.max_hours` (bawaan 6 jam) atau jam 05:00, mana yang lebih dulu,
  menurut jam PC. Bilang "mode tamu" lagi selagi aktif menghitung ulang dari saat itu.
- Berakhirnya diam-diam: dia tidak mengumumkannya, dan obrolan berikutnya mulai baru dengan
  ingatannya.
- Hanya diingat selama `waifu run` berjalan: restart atau crash juga mengakhirinya.

Untuk mengubah lamanya, tulis di `config.yaml`, lalu jalankan ulang `waifu run`:

```yaml
guest_mode:
  max_hours: 4    # 1-24; paling lambat tetap jam 05:00
```

### Verifikasi suara

Dengan verifikasi suara menyala (bawaan), suara tamu memang tidak dilayani, jadi mode tamu menjaga
apa yang dia ucapkan di depan tamu. Dengan `speaker.enabled: false` atau
`waifu run --no-speaker-check`, siapa pun bisa bilang "mode tamu selesai". `waifu check` hanya
memperingatkan yang pertama (baris `!` di bawah): ia berjalan terpisah dari `waifu run`, jadi
tidak melihat `--no-speaker-check`, dan barisnya tetap ✓.

```text
✓ Mode tamu: "mode tamu" dan "mode tamu selesai" lewat suara, chat, atau HP; berakhir sendiri setelah 6 jam atau jam 05:00
! Mode tamu: "mode tamu" dan "mode tamu selesai" lewat suara, chat, atau HP; berakhir sendiri setelah 6 jam atau jam 05:00; verifikasi suara mati, jadi suara tamu juga bisa mengakhirinya
```

### Privasi mode tamu

- Tidak ada yang baru disimpan atau dikirim: mode tamu hanya diingat di memori `waifu run` (atau
  `waifu chat`). Dengan Claude, panggilan `guest_mode` ikut ke API Anthropic seperti tool lain.
- Log INFO mencatat kapan mode tamu menyala, mati, atau berakhir sendiri, tool yang ditolak
  (namanya saja), dan pertanyaan yang dilewati (tanpa isinya).
- Yang tidak ditutup mode tamu: pengingat dan pemberitahuan di atas, notifikasi HP yang memang
  dikirim fitur lain, perintah terminal, dan log (`Heard ...` tetap mencatat kalimat Master).

## Ulangi

Kalau Master tidak menangkap yang Cyrene ucapkan (AC berisik, sedang tidak memperhatikan),
"Cyrene, ulangi" membuatnya mengucapkan kalimat terakhirnya lagi, kata per kata, seketika, tanpa
bertanya ke otaknya (tanpa token dan tanpa menunggu). Selalu tersedia, dengan otak Ollama maupun
Claude, tanpa pengaturan atau paket tambahan.

```text
Cyrene : "Besok hujan mulai jam tiga sore, Master. Jangan lupa payung."
Master : "Hah?"
Cyrene : "Besok hujan mulai jam tiga sore, Master. Jangan lupa payung."
```

### Kalimat yang dikenali

Seluruh ucapan Master harus hanya permintaan mengulang. Namanya, "Master", dan kata seperti
"dong", "deh", "ya", "coba", dan "tolong" boleh ikut ("tolong diulangi ya", "ulangi dong,
Cyrene").

- "ulangi", "ulangi lagi", "ulang lagi", "diulang", "diulangi", "bisa diulang?", "bisa
  diulangi?"
- "tadi bilang apa?", "tadi kamu bilang apa?", "kamu tadi bilang apa?", "kamu bilang apa
  tadi?", "bilang apa tadi?", "apa tadi?"
- "sekali lagi", "hah?", "maaf, apa?", dan semuanya dengan "maaf" di depan ("maaf, tadi bilang
  apa?")

Dalam 8 detik sesudah dia bicara (`assistant.followup_seconds`), "ulangi" saja cukup, tanpa
namanya. Ucapan yang memuat kata lain tetap dijawab otaknya seperti biasa: "ulangi lagunya",
"putar sekali lagi", "apa tadi lampunya sudah mati?", "tadi aku bilang apa?", dan juga "apa?"
saja. "Coba sekali lagi" dan "tolong sekali lagi" (misalnya sesudah perintah yang gagal) berarti
minta dicoba lagi, bukan diulang, jadi juga dijawab otaknya.

### Yang diulang

- Jawaban terakhirnya untuk Master di PC, dengan ekspresi avatar yang sama.
- Yang dia ucapkan sendiri sekaligus: pengingat, kabar game, peringatan hujan, dan pemberitahuan
  lain, juga jawabannya untuk sapaan pagi atau pertanyaan malam jurnal.
- Jawaban yang dipotong (double-click pada avatar) atau gagal diucapkan diulang utuh.
- Volumenya volume suaranya saat ini, termasuk mode bisik (lihat
  [Volume suaraku](#volume-suaraku)).

Yang tidak diulang: "はい、Master?" saat hanya dipanggil namanya, reaksi avatar saat dielus atau
di-double-click, kalimat "Cyrene siap, Master." saat mulai, dan balasan di HP. Kalau belum ada
yang bisa diulang, dia bilang "Belum ada yang bisa kuulangi, Master."

"Sekali lagi" sesudah "Sudah, Master." hanya mengulang kata-katanya, tidak menjalankan
perintahnya lagi. Untuk itu bilang "nyalakan lagi" atau "putar lagi lagunya". Otaknya juga tidak
tahu Master minta diulang: obrolannya tetap seperti sebelum "ulangi".

### Pertanyaan konfirmasi

Saat dia bertanya "Komputer mau kumatikan sekarang, Master? Jawab iya kalau jadi." (lihat
[Menyalakan dan mematikan PC](#menyalakan-dan-mematikan-pc)), "ulangi" mengulang pertanyaannya
tanpa menjawabnya, dan "iya" sesudahnya tetap menjalankannya (dalam 8 detik sesudah pengulangan,
tanpa nama). Begitu juga pertanyaan mencatat pengeluaran di [tepira](#keuangan-dari-tepira).
Batas satu menit untuk menjawab tetap dihitung sejak perintahnya pertama kali ditahan.

### Saat fokus, main game, dan mode tamu

- Selama [teman fokus](#teman-fokus) atau [mode game](#mode-game) menahan pengumuman, "ulangi"
  tetap dijawab. Pengumuman yang tertahan belum pernah diucapkan, jadi tidak ikut diulang, dan
  tetap menunggu sampai fokus atau gamenya selesai.
- Begitu [mode tamu](#mode-tamu) menyala, mati, atau berakhir sendiri (juga dari HP), yang dia
  ucapkan sebelumnya dilupakan, dan "ulangi" dijawab "Belum ada yang bisa kuulangi": tamu tidak
  bisa memintanya mengulang hal pribadi yang Master dengar sebelum mereka datang. Jawaban yang
  menyalakan atau mematikan mode tamu itu sendiri juga tidak diulang, karena bisa memuat hal
  pribadi yang diambil sebelum mode tamu menyala ("berapa saldoku, terus nyalakan mode tamu").
  Dengan verifikasi suara mati, tamu bisa meminta pengulangan apa yang dia ucapkan sejak mode
  tamu menyala.

### Di HP

Di [HP](#cyrene-di-hp) kalimatnya sudah tertulis di layar, jadi "ulangi" yang diketik atau
diucapkan di HP dikirim ke otaknya seperti pesan lain (memakai satu giliran). Balasan di HP tidak
pernah diulang di PC.

Tidak ada yang disimpan atau dikirim: kalimat terakhirnya hanya diingat di memori selama
`waifu run` berjalan, dan pengulangannya hanya mencatat `Repeating what she said last` di log,
tanpa isinya (pengingat dan pengumuman sudah tercatat sebagai `Announcing: ...` saat pertama
diucapkan).

## Belajar bahasa Jepang

Cyrene bisa mengajari Master satu kata Jepang setiap hari dan menguji kata yang sudah dipelajari,
tanpa aplikasi lain dan tanpa internet: daftar 300 kata dan ungkapan tingkat pemula (JLPT N5)
sudah ikut terpasang bersama Cyrene, masing-masing dengan cara baca kana, tulisan kanji kalau
biasa dipakai, arti dalam bahasa Indonesia, dan satu contoh kalimat pendek. Fitur ini **mati dari
awal**; dengan otak Ollama maupun Claude, tanpa paket tambahan.

```text
(07.05)  Cyrene : "おはよう, Master! ... Kata Jepang hari ini: ちいさい, artinya kecil."
Master : "Cyrene, kata Jepang hari ini apa?"
Cyrene : "ちいさい, artinya kecil. Contohnya 小さいかばんを買いました, saya membeli tas kecil."
Master : "Cyrene, kuis bahasa Jepang dong."
Cyrene : "Boleh! ちいさい artinya apa?"
Master : "Kecil."                          (tanpa memanggil nama: jendela lanjutan terbuka)
Cyrene : "Benar, hebat! Belum ada kata lain yang perlu diulang hari ini."
Master : "Cyrene, aku sudah belajar berapa kata?"
```

### Menyalakan

Tulis di `config.yaml`, lalu jalankan ulang `waifu run`:

```yaml
japanese:
  enabled: true
  path: data/japanese.json   # kata yang sudah dipelajari dan jadwal ulangannya, hanya di PC ini
```

### Kata hari ini

- **Satu kata baru setiap hari**, sama sepanjang hari. Urutannya acak tapi tetap, dan semua 300
  kata keluar dulu sebelum ada yang berulang (sekitar sepuluh bulan).
- [Sapaan pagi](#sapaan-pagi-dan-ringkasan-harian) menyebutnya di akhir: katanya dengan kana, lalu
  artinya. Hanya sapaan itu: pertanyaan cuaca atau jadwal di jam lain tidak ikut menyebutnya.
  "Kata Jepang hari ini apa?" bisa kapan saja, dengan contoh kalimat dan artinya.
- Kata itu dihitung sudah dipelajari begitu dia menyebutnya (di sapaan pagi atau saat ditanya).
  Hari tanpa sapaan dan tanpa ditanya, kata hari itu terlewat sampai putaran berikutnya.
- Bahasa Jepangnya ditulis dengan kana atau kanji, jadi diucapkan dengan suara Jepang, dan artinya
  dengan suara Indonesia. Tidak ada romaji.

### Kuis

- **Hanya kalau Master minta** ("kuis bahasa Jepang dong", "tes kosakata Jepang"). Dia menanyakan
  satu kata saja: katanya dengan kana, lalu artinya apa, dan menunggu jawaban Master.
- Jawab dengan artinya dalam bahasa Indonesia. Dia sendiri yang menilai jawabannya: sinonim atau
  arti yang dekat dihitung benar. Karena Master hanya mendengar bunyinya, arti kata lain yang
  bunyinya sama juga dihitung benar (はな bisa bunga atau hidung), atau dia memberi petunjuk lalu
  bertanya lagi. Kalau benar dia memuji, kalau salah dia menyebut artinya dengan lembut, lalu
  menanyakan kata berikutnya kalau Master masih mau. Tanpa ceramah tata bahasa.
- **Jadwal ulangannya** (sistem Leitner, lima kotak): kata baru masuk kotak 1 dan sudah bisa
  ditanya hari itu juga. Jawaban benar menaikkannya satu kotak dan menunda ulangannya 2, 4, 8,
  lalu 16 hari; jawaban salah mengembalikannya ke kotak 1, ditanya lagi besok. Kata di kotak 5
  dihitung dikuasai (tetap diulang setiap 16 hari). Yang ditanya dulu: kata yang sudah waktunya
  diulang, mulai dari kotak terendah.
- Kalau belum ada yang perlu diulang, dia bilang kapan kata berikutnya waktunya.
- Tidak pernah di sapaan pagi, laporan game mingguan, atau pertanyaan jurnal malam: kuis dan
  penilaian hanya di giliran Master sendiri.
- "Aku sudah belajar berapa kata?": berapa kata yang sudah dipelajari, yang dikuasai, dan yang
  perlu diulang hari ini.

### Kata dari Master sendiri

Kata Jepang yang Master temui sendiri (misalnya lewat [Artinya apa](#artinya-apa)) bisa ikut
dilatih: "simpan kata itu" atau "masukin ke latihan" (sesudah terjemahannya cukup tanpa nama).

- Dia menyimpan cara bacanya (1-20 huruf kana), tulisan kanjinya kalau ada (1-20 huruf, minimal
  satu kanji), dan artinya (satu baris, paling banyak 80 huruf). Kata baru masuk kotak 1 dan
  sudah bisa ditanya hari itu juga, lalu ikut kuis dan jadwal ulangan seperti kata dari daftar.
- Kata yang ada di daftar 300 kata (cara baca sama, dan kanji sama atau salah satunya tanpa kanji)
  dipelajari dari daftar itu, bukan disimpan dua kali; kata yang sudah pernah disimpan juga tidak
  disimpan lagi.
- Paling banyak 200 kata. Belum bisa dihapus atau diubah lewat suara: untuk itu, sunting
  `data\japanese.json` (bagian `custom` dan kode kata `c1`, `c2`, ... di `words`) saat Cyrene
  mati.
- Kata hari ini tetap dari daftar 300 kata. "Aku sudah belajar berapa kata?" menyebut kata
  tambahan Master terpisah.
- Hanya atas permintaan Master: tidak di sapaan pagi atau hal lain yang dia mulai sendiri, dan
  tidak selama [mode tamu](#mode-tamu).
- Versi Cyrene sebelum v1.56 tidak mengenal kata tambahan: kalau Master kembali ke versi lama,
  `data\japanese.json` dipindah ke `japanese.json.corrupt-<waktu>` (tidak dihapus).

### Melihat di terminal: `waifu jepang`

```powershell
waifu jepang            # kata hari ini (sama dengan: waifu jepang hari-ini)
waifu jepang progres    # kemajuan Master
```

```text
Kata hari ini, Sabtu 24-10-2026: ちいさい (小さい), artinya kecil
Contoh: 小さいかばんを買いました。
Artinya: Saya membeli tas kecil.
Kata hari ini dihitung sudah dipelajari saat Cyrene menyebutnya, di sapaan pagi atau saat ditanya.
```

```text
Dipelajari: 12 dari 300 kata, 2 dikuasai
Perlu diulang hari ini: 3 kata
Kata tambahanmu: 3 kata, 1 perlu diulang hari ini.
```

Baris terakhir hanya muncul kalau Master sudah menyimpan kata sendiri.

Perintah ini hanya membaca: tidak mencatat kata hari ini, tidak membuat file, dan tidak bisa
menguji (tanya Cyrene saja). Jalan juga saat `japanese.enabled: false` (dengan peringatan di baris
pertama) dan selagi `waifu run` berjalan; exit code 1 kalau filenya tidak bisa dibaca. Untuk mulai
dari awal, hapus `data\japanese.json` saat Cyrene mati.

`waifu check` menampilkan `✓ Belajar bahasa Jepang: 12 dari 300 kata dipelajari, 2 dikuasai` atau
`! Belajar bahasa Jepang mati`. File `data\japanese.json` yang rusak dipindah ke
`japanese.json.corrupt-<waktu>` (kemajuan dimulai dari kosong), dan `check` menunjuknya sampai
Master memperbaiki atau menghapusnya.

### Mode tamu

Tetap jalan di [mode tamu](#mode-tamu): kata pemula dan berapa kata yang Master hafal bukan hal
pribadi, jadi belajar bersama teman juga boleh. Di mode tamu sapaan paginya cukup cuaca dan jadwal
perangkat, jadi kata hari ini bisa terlewat di sapaan; tanyakan saja. Hanya menyimpan kata baru
yang ditolak selama mode tamu, supaya kata tamu tidak masuk ke latihan Master.

### Privasi belajar bahasa Jepang

`data\japanese.json` hanya berisi kode kata (misalnya `w214`), kotaknya, dan tanggalnya, ditambah
kata yang Master simpan sendiri (cara baca, kanji, arti, dan tanggalnya), hanya di PC ini;
`waifu run` dan `waifu chat` sama-sama mencatat ke file ini. Dengan Claude, kata dan jawaban
Master ikut ke API Anthropic seperti obrolan biasa (dengan Ollama, semuanya lokal). Tidak ada
notifikasi HP sendiri: kalau `briefing.push` menyala, teks sapaan pagi (dengan katanya) ikut
dikirim seperti biasa.

## Pengingat istirahat

Selama `waifu run`, Cyrene bisa mengingatkan Master beristirahat setelah lama di depan layar:
setelah satu jam terus memakai keyboard atau mouse, dia bilang sekali dengan lembut, lalu menunggu
satu jam lagi. Jeda sebentar dari PC dihitung istirahat, dan hitungannya mulai lagi dari nol.
Fitur ini **mati dari awal**; dengan otak Ollama maupun Claude, tanpa paket tambahan.

```text
(21.00)  Cyrene : "Sayang, sudah satu jam di depan layar. Istirahatkan matamu sebentar dan minum air, ya."
(22.00)  Cyrene : "Sayang, sudah dua jam di depan layar. Istirahatkan matamu sebentar dan minum air, ya."
```

### Menyalakan

Tulis di `config.yaml`, lalu jalankan ulang `waifu run`:

```yaml
breaks:
  enabled: true
  every_minutes: 60        # menit terus di depan layar sebelum dia mengingatkan (20-180)
  idle_reset_minutes: 5    # jeda tanpa keyboard dan mouse selama N menit dihitung istirahat (2-15)
  during_games: false      # true: tetap diucapkan selagi mode game menahan pengumuman
  push: false              # true: juga dikirim ke HP (butuh notify)
  active_from: "08:00"     # jam aktif, boleh tidak ditulis (keduanya): sepanjang hari
  active_until: "02:00"    # boleh melewati tengah malam
```

Hanya `enabled: true` yang perlu ditulis; tanpa yang lain berlaku nilai di atas, tapi sepanjang
hari. `waifu chat` tidak punya pengingat istirahat.

### Yang dihitung

- **Aktif** artinya keyboard atau mouse dipakai tanpa jeda sepanjang `idle_reset_minutes` (bawaan
  5 menit). Membaca beberapa menit tanpa menyentuh apa pun tetap dihitung aktif selama jedanya
  lebih pendek dari itu.
- **Istirahat** artinya jeda tanpa keyboard dan mouse minimal `idle_reset_minutes`, atau PC yang
  sleep. Sesudahnya hitungannya mulai lagi dari nol saat Master kembali. Dia melihat setiap 30
  detik, jadi jeda yang hanya sedikit lebih panjang dari `idle_reset_minutes` bisa terlewat; jeda
  yang setengah menit lebih panjang selalu terhitung.
- **Kalimatnya tepat tiap jam** (tiap `every_minutes`) sejak hitungan dimulai: satu jam, dua jam,
  tiga jam, masing-masing sekali.
- **Tidak bicara ke kursi kosong:** kalau Master sudah berdiri saat waktunya tiba, kalimatnya
  menunggu sentuhan keyboard atau mouse berikutnya; kalau Master pergi sepanjang
  `idle_reset_minutes`, itu sudah istirahat dan kalimatnya tidak diucapkan.
- Dihitung sejak `waifu run` mulai: restart `waifu run` mulai dari nol.

### Selama fokus dan main game

- **Tidak pernah di tengah blok [fokus](#teman-fokus)**: teman fokus punya istirahatnya sendiri.
  Selama blok fokus tidak ada yang dihitung, dan sesudahnya hitungannya mulai dari nol.
- **[Mode game](#mode-game)** dengan `hold` atau `phone`: selagi game terbuka kalimatnya ikut
  ditahan seperti pemberitahuan lain, dan dilewati kalau belum terucap setelah
  `idle_reset_minutes` karena sudah basi. Hitungannya tetap jalan, jadi kalimat berikutnya datang
  `every_minutes` kemudian. Dengan `phone` dan `push: true`, yang sudah ke HP tidak diucapkan
  selama main.
- **`during_games: true`**: selagi mode game menahan pengumuman, dia tetap mengucapkannya seperti
  pengingat yang Master pasang sendiri, juga di tengah obrolan.
- Seperti pemberitahuan lain, dia menunggu jendela lanjutan tertutup, jadi obrolan tidak dipotong;
  kalimat yang tertahan obrolan lebih lama dari `idle_reset_minutes` juga dilewati.

### Jam aktif

Dengan `active_from` dan `active_until`, di luar jam itu tidak ada yang dihitung atau diucapkan,
dan hitungannya mulai dari nol saat jam aktif dimulai. Boleh melewati tengah malam (08:00 sampai
02:00); tulis keduanya, atau tidak sama sekali untuk sepanjang hari.

### Batasan

- **Main dengan stik (controller) tidak terbaca sebagai aktif**: Windows tidak menghitungnya
  sebagai sentuhan keyboard atau mouse, jadi main dengan stik, seperti menonton video tanpa
  menyentuh apa pun, dianggap istirahat.
- Sentuhan apa pun dihitung aktif: tombol media yang Cyrene tekan sendiri, mouse jiggler, main
  jarak jauh (Parsec, Steam Remote Play), atau mouse yang tersenggol.

`waifu check` menampilkan salah satu baris ini (jam aktif, `juga saat main`, dan `juga ke HP`
hanya kalau diatur; `juga ke HP` butuh notifikasi HP menyala):

```text
✓ Pengingat istirahat: tiap 60 menit di depan layar, jeda 5 menit dihitung istirahat
✓ Pengingat istirahat: tiap 60 menit di depan layar, jeda 5 menit dihitung istirahat, jam 08:00-02:00, juga saat main, juga ke HP
! Pengingat istirahat mati
```

### Privasi pengingat istirahat

Tidak ada yang disimpan: yang dibaca hanya berapa lama sejak keyboard atau mouse terakhir
disentuh, bukan tombol yang ditekan, posisi mouse, layar, atau aplikasi yang terbuka. Hitungannya
hanya diingat selama `waifu run` berjalan dan hanya dicatat di log DEBUG; log INFO mencatat
kalimatnya (`Announcing: ...`) saat diucapkan, seperti pemberitahuan lain. Kalimatnya ke
`ntfy.sh` hanya dengan `breaks.push` dan notifikasi HP menyala. Mode tamu tidak mengubah apa pun:
kalimatnya tidak menyebut hal pribadi.

## Waktu sholat

Cyrene bisa memberi tahu waktu sholat untuk tempat Master (imsak, subuh, terbit, dzuhur, ashar,
maghrib, isya) saat ditanya, dan kalau Master mau, bilang sekali saat masuk waktu tiap sholat.
Waktunya dihitung di PC ini dengan metode Kemenag RI, tanpa internet dan tanpa akun. Fitur ini
**mati dari awal**: selama mati, tidak ada tool, pengingat, atau kalimat soal sholat sama sekali.
Bisa dengan otak Ollama maupun Claude, tanpa paket tambahan.

```text
Master : "Cyrene, jadwal sholat hari ini?"
Cyrene : "Di Bandung hari ini Subuh jam 04.08, Dzuhur 11.36, Ashar 14.47, Maghrib 17.45, dan Isya 18.57."
(11.36)  Cyrene : "Sayang, sudah masuk waktu Dzuhur."
```

### Menyalakan

Tulis di `config.yaml`, lalu jalankan ulang `waifu run`:

```yaml
prayer:
  enabled: true
  latitude: -6.9175        # boleh tidak ditulis: tempat di bagian briefing yang dipakai
  longitude: 107.6191      # diisi bersama latitude
  ihtiyath_minutes: 2      # menit pengaman yang ditambahkan ke tiap waktu sholat (0-5)
  remind: [subuh, dzuhur, ashar, maghrib, isya]  # sholat yang diingatkan ([] = tanpa pengingat)
  minutes_before: 0        # N menit sebelum waktunya (0 = tepat saat masuk waktu, paling lama 30)
  push: false              # true: juga dikirim ke HP (butuh notify)
```

Hanya `enabled: true` yang perlu ditulis; tanpa yang lain berlaku nilai di atas.

### Tempatnya

- `prayer.latitude` dan `prayer.longitude` kalau ditulis (paling tepat: koordinat rumah, misalnya
  dari Google Maps), kalau tidak koordinat `briefing.latitude`/`longitude`, kalau tidak
  `briefing.city` (lihat [Sapaan pagi](#sapaan-pagi-dan-ringkasan-harian)).
- Kota dicari sekali lewat Open-Meteo, seperti untuk cuaca, saat pertama dibutuhkan (bukan saat
  `waifu run` mulai), lalu diingat sampai `waifu run` berhenti. Kota yang dicari adalah pusat
  kotanya, jadi waktunya bisa beda semenit dengan rumah di pinggir kota.
- Nama tempat yang dia sebut adalah `briefing.city`, seperti untuk cuaca: dengan koordinat di
  `prayer` dan `city: Bandung` dia bilang "di Bandung" (jadi pastikan keduanya menunjuk tempat
  yang sama). Tanpa `city` dia tidak menyebut tempat: koordinatnya tidak pernah ada di jawaban
  tool-nya, jadi tidak diucapkan dan tidak ikut ke API Anthropic. `waifu sholat` dan
  `waifu check` tetap menampilkannya di terminal.
- Tanpa satu pun, dia bilang lokasinya belum diatur.

### Bertanya

"Jadwal sholat hari ini?", "Maghrib jam berapa?", "jadwal sholat besok", atau "Isya tanggal 1
November jam berapa?": dia membacakan waktu dari perhitungannya, bukan dari ingatannya, untuk hari
ini atau tanggal lain sampai setahun ke depan atau ke belakang. Untuk jadwal dia menyebut kelima
waktu sholat; imsak dan terbit hanya kalau ditanya. Dia hanya menyebut waktunya: tidak
pernah menyuruh atau menanyakan apakah Master sudah sholat. Di sapaan pagi, laporan game mingguan,
dan pertanyaan jurnal dia tidak membahasnya. `waifu chat` juga bisa ditanya.

### Pengingat

- Selama `waifu run`, untuk tiap sholat di `remind` dia bilang sekali "Sayang, sudah masuk waktu
  Dzuhur." saat masuk waktunya. Dengan `minutes_before: 10`: "Sayang, sepuluh menit lagi masuk
  waktu Dzuhur." (sekali saja, tidak diulang saat masuk waktunya).
- Karena Master sendiri yang menyalakannya, pengingatnya diucapkan seperti pengingat yang Master
  pasang sendiri: juga di tengah [blok fokus](#teman-fokus), selagi [mode game](#mode-game)
  menahan pengumuman (juga dengan `phone`), dan saat jendela lanjutan terbuka. Kalau dia sedang
  bicara, pengingatnya menyusul sesudahnya.
- Tiap sholat hanya sekali sehari, juga kalau `waifu run` dijalankan ulang: yang sudah diucapkan
  dicatat di `data\prayer.json` (`prayer.state_path`) sebelum diucapkan.
- Yang terlewat lebih dari 30 menit (PC baru menyala, bangun dari sleep, atau `waifu run` baru
  dijalankan) tidak diucapkan lagi.
- Dengan `push: true` dan notifikasi HP menyala, kalimatnya juga dikirim ke HP (dengan ikon
  masjid).
- `remind: []` mematikan pengingatnya; jadwalnya tetap bisa ditanya. `waifu chat` tidak punya
  pengingat.

### Melihat di terminal

`waifu sholat` menampilkan waktu hari ini, `waifu sholat 2026-11-01` tanggal lain (di sini
dengan koordinat di `prayer` dan `briefing.city: Bandung`; tanpa `city`, baris pertamanya
menyebut koordinatnya):

```text
> waifu sholat
Waktu sholat di Bandung, Sabtu 24-10-2026 (WIB):
  Imsak    03:58
  Subuh    04:08
  Terbit   05:22
  Dzuhur   11:36
  Ashar    14:47
  Maghrib  17:45
  Isya     18:57
Metode Kemenag RI: Subuh -20°, Isya -18°, Ashar bayangan 1x, ihtiyath 2 menit.
```

Perintah ini tetap jalan saat fitur mati (dengan peringatan `! Waktu sholat mati`), dan selesai
dengan kode 1 kalau lokasinya belum diatur atau kotanya tidak bisa dicari.

`waifu check` menampilkan salah satu baris ini (tanpa mencari kota):

```text
✓ Waktu sholat: untuk Bandung (-6.9175, 107.6191), ihtiyath 2 menit, pengingat subuh, dzuhur, ashar, maghrib, isya
✓ Waktu sholat: untuk -6.9175, 107.6191, ihtiyath 2 menit, pengingat subuh, maghrib 10 menit sebelumnya, juga ke HP
✓ Waktu sholat: untuk Bandung, ihtiyath 2 menit, tanpa pengingat
! Waktu sholat: lokasi belum diatur (isi briefing.city, atau prayer.latitude dan prayer.longitude)
! Waktu sholat mati
```

Kalau `data\prayer.json` rusak (misalnya diubah tangan), file itu dipindah ke
`data\prayer.json.corrupt-<waktu>` dan dia mulai baru (pengingat hari itu bisa terucap sekali
lagi); `waifu check` menunjuk file itu sampai dihapus.

### Metode dan batasannya

- Kemenag RI: Subuh saat matahari 20° di bawah ufuk, Isya 18°, Ashar saat bayangan sama dengan
  panjang benda ditambah bayangan tengah hari (Syafi'i), Maghrib saat matahari terbenam, Dzuhur
  saat matahari di puncak. Ihtiyath (bawaan 2 menit) ditambahkan ke tiap waktu sholat lalu
  dibulatkan ke atas; terbit dikurangi ihtiyath dan dibulatkan ke bawah; imsak 10 menit sebelum
  Subuh.
- Hasilnya bisa beda satu atau dua menit dengan jadwal resmi Kemenag (koordinat kota yang mereka
  pakai, koreksi ketinggian tempat, dan data posisi mataharinya). Untuk ibadah, jadwal resmi atau
  masjid setempat tetap jadi patokan.
- Waktunya selalu WIB: tempat di WITA, WIT, atau luar negeri tetap ditampilkan dalam WIB.
- Jauh dari khatulistiwa, ada waktu yang tidak terjadi (misalnya Isya di musim panas di Eropa
  utara): ditampilkan `--` dan tidak diingatkan hari itu.
- Pengingat bisa telat sampai 30 detik (dia melihat tiap 30 detik), dan lebih lama kalau sedang
  bicara.

### Mode tamu

Mode tamu tidak mengubah apa pun: jadwalnya tetap bisa ditanya (waktu sholat untuk kota yang
sudah disebut cuaca bukan hal pribadi, dan koordinat rumah tidak pernah dia sebut), dan
pengingatnya tetap diucapkan.

### Privasi waktu sholat

Waktunya dihitung di PC ini; yang dikirim ke Open-Meteo hanya nama kota, seperti untuk cuaca
(tidak ada sama sekali dengan koordinat). `data\prayer.json` hanya berisi tanggal dan pengingat
mana yang sudah diucapkan hari itu, bukan apakah Master sholat. Dengan Claude, hasil tool-nya
(nama kotanya, kalau ada, dan waktunya; tidak pernah koordinatnya) ikut ke API Anthropic seperti
obrolan biasa. Log INFO mencatat
kalimatnya (`Announcing: ...`) saat diucapkan, seperti pengingat lain. Kalimatnya ke `ntfy.sh`
hanya dengan `prayer.push` dan notifikasi HP menyala.

## Tombol cepat di PC

[Tombol cepat](#tombol-cepat-di-hp) yang sama dengan di HP juga bisa dipakai di PC, di browser
PC ini: lampu, AC, colokan, dan rutinitas, sekali klik, tanpa bicara ke Cyrene dan tanpa biaya
API. Tidak perlu `waifu run` (kecuali untuk langkah rutinitas yang ditunda, lihat di bawah),
avatar, atau Tailscale, dan tidak ada pengaturan baru.

```powershell
.venv\Scripts\waifu tombol             # buka halaman Tombol cepat di browser PC ini
.venv\Scripts\waifu tombol --dry-run   # dengan perangkat simulasi, untuk mencoba
```

```text
Tombol cepat di PC: http://127.0.0.1:53211/t/Gz7mNcckSlaxLMFcD9ptmJzD6qRMkzBA/. Tekan Ctrl+C untuk menutup.
```

### Memakai

- Browser default PC ini membuka halaman **Tombol cepat**: satu kartu per perangkat dan satu
  kartu **Rutinitas**, dengan tombol yang sama seperti di HP. `phone.quick_actions` memilih dan
  mengurutkannya untuk keduanya (lihat [Tombol cepat di HP](#tombol-cepat-di-hp)), juga kalau
  `phone.enabled: false`. Klik satu tombol: hasilnya muncul 6 detik di bawah jendela ("Lampu
  kamar dinyalakan.", atau alasan perangkatnya gagal).
- `config.yaml` dibaca sekali, saat `waifu tombol` dijalankan. Setelah mengubah perangkat,
  rutinitas, atau `phone.quick_actions` di `config.yaml`, tutup `waifu tombol` (Ctrl+C) lalu
  jalankan lagi; memuat ulang halaman atau menjalankan ulang `waifu run` saja tidak cukup.
- Halamannya membaca daftar tombol lagi setiap 15 detik, saat jendelanya aktif lagi, dan
  langsung sesudah tombol alarm ditekan: selagi
  [alarm](#alarm-bangun) `waifu run` berbunyi, grup **Alarm** (**Matikan alarm**, **Tunda 5
  menit**) muncul paling atas. Tanpa tombol perangkat atau rutinitas sama sekali, `waifu tombol`
  tidak membuka halaman, jadi tombol alarmnya pun tidak ada: pakai HP atau suara.
- **Jendela terminalnya harus tetap terbuka** selama halaman dipakai; Ctrl+C menutupnya
  ("Selesai."). Halaman yang masih terbuka sesudahnya bilang "Tombol cepat di PC sudah ditutup.
  ...". Alamatnya baru setiap kali `waifu tombol` dijalankan (port dan token baru), jadi tidak
  bisa dijadikan bookmark.
- Kalau browser tidak bisa dibuka otomatis, terminal bilang begitu: buka sendiri alamat yang
  tercetak (di Windows Terminal cukup Ctrl+klik). Halamannya tetap jalan.
- **PC tidak pernah jadi tombol**, sama seperti di HP: tidak ada tombol untuk mematikan,
  me-restart, menidurkan, atau menyalakan PC, dan tidak ada tombol untuk colokan PC.
- Satu tombol berjalan pada satu waktu, paling banyak 10 tombol per menit untuk semua tab
  bersama, seperti di HP. Tombol di PC dan di HP berjalan di program yang berbeda, jadi
  keduanya bisa jalan bersamaan, seperti `waifu device` selagi `waifu run` berjalan.
- Rutinitas dengan langkah yang ditunda menaruh langkah itu di jadwal Cyrene
  (`data\schedule.json`), yang dijalankan `waifu run` tepat waktunya, sama seperti
  `waifu routine run`: **langkah itu hanya jalan kalau `waifu run` menyala pada jamnya**. Kalau
  ada tombol seperti itu, terminal mengingatkannya saat `waifu tombol` dibuka, dengan tanda `!`
  kalau `waifu run` sedang tidak berjalan. Dengan `--dry-run` jadwalnya sementara dan hilang saat
  ditutup.
- Tanpa tombol sama sekali, `waifu tombol` hanya bilang `! Belum ada tombol cepat: ...` dan
  tidak membuka apa pun. `waifu check` punya baris `✓ Tombol cepat di PC: 7 tombol, buka dengan
  'waifu tombol'`.
- Cyrene tidak tahu tombol yang ditekan (tanya lagi untuk keadaan sekarang), dan
  [mode tamu](#mode-tamu) tidak mengubah apa pun: tombolnya tetap jalan, seperti di HP.

### Siapa yang bisa menekan

Hanya halaman yang dibuka `waifu tombol` di browser PC ini:

- server-nya hanya mendengarkan di `127.0.0.1`, jadi komputer atau HP lain di Wi-Fi yang sama
  tidak bisa membukanya;
- alamatnya memuat token acak yang baru setiap kali; tanpa token itu setiap permintaan dijawab
  "Tidak ditemukan.";
- situs lain yang terbuka di browser yang sama tidak bisa menekan tombol atau membaca tokennya:
  tombol hanya diterima dari halaman itu sendiri, dan nama situs yang diarahkan ke 127.0.0.1
  ditolak.

Program lain di akun Windows yang sama memang bisa mengendalikan perangkat (seperti
`waifu device`), jadi itu di luar yang dijaga halaman ini.

### Privasi tombol cepat di PC

Tidak ada yang disimpan atau dikirim ke luar PC, dan otak Cyrene tidak terlibat. Tokennya hanya
ada di alamat yang dibuka browser (riwayat browser menyimpannya, tapi tidak berlaku lagi begitu
halamannya ditutup) dan di terminal, tidak pernah di log, juga dengan `-v`. Terminal mencatat
tombol dan hasilnya (`PC page ran quick action dev.lampu_kamar.on: ok`).

## Diagnosa suara

Kalau Cyrene diam saja, biasanya salah satunya: mic yang dia pakai tidak mendengarmu (misalnya
colokan mic di PC yang kosong, bukan headset), suaramu tidak cukup keras untuk batas deteksi
bicaranya, suaramu dianggap suara orang lain, Whisper tidak menangkap namanya, atau tidak ada kata
yang tertangkap. Diagnosa suara menunjukkan yang mana, tanpa membuka log, dan Cyrene sendiri bisa
ditanya "tadi kamu denger apa?". Tidak perlu pengaturan baru; halaman Suara butuh
[Cyrene di HP](#cyrene-di-hp).

### Halaman Suara di HP

Di [halaman HP](#cyrene-di-hp), tombol **Suara** di bilah atas membuka lembar **Diagnosa suara**
yang diperbarui tiap 1,5 detik selama terbuka:

- **Mic yang dipakai**: nama mic dan host API-nya (misalnya `Headset (Polaris-BT) · MME`), dan mic
  default Windows saat Cyrene mulai.
- **Bar level**: bagian paling keras dari detik terakhir (satu potongan sekitar 30 ms, yang sama
  dengan yang dibandingkan deteksi bicaranya), dengan garis merah di batas suara: suara baru
  dianggap bicara kalau mencapai garis itu, dan bar-nya menjadi hijau. Kalau saat kamu bicara bar
  itu tidak pernah melewati garis, mic itu tidak cukup jelas mendengarmu: ganti mic, dekatkan, atau
  turunkan ambang VAD.
- **Yang terdengar**: 50 ucapan terakhir, terbaru di atas, masing-masing dengan jam, panjang,
  puncak suara, apa yang ditangkap Whisper, skor kecocokan suaramu dibanding batasnya, dan lama
  Whisper, otak, dan suaranya, berwarna sesuai yang dia lakukan:

  | Tanda | Artinya |
  |---|---|
  | dijawab (hijau) | untuknya, dan sudah dia jawab |
  | tanpa nama (kuning) | ada kata-kata, tapi tanpa namanya dan di luar 8 detik sesudah dia bicara |
  | bukan suaramu (merah) | skornya di bawah `speaker.threshold`, atau verifikasi suara sedang tidak jalan; Whisper tidak dijalankan, jadi tidak ada teksnya |
  | tak ada kata (ungu) | ada suara, tapi Whisper tidak menangkap kata apa pun |
  | terlalu pendek (abu-abu) | bunyi pendek (klik, batuk, ketukan) yang tidak dianggap ucapan |

Selama [mode tamu](#mode-tamu) menyala, tombol Suara hilang dan halamannya ditolak (catatannya
memuat yang diucapkan), dan sesudah mode tamu berganti, catatan sebelumnya tidak ditampilkan lagi.

### Ganti mic dari HP

Pilih mic di **Ganti mic** lalu ketuk **Pakai**: Cyrene langsung pindah tanpa dijalankan ulang, dan
pilihan itu tetap dipakai sesudah `waifu run` dijalankan ulang. Daftarnya mic yang dikenali PC
saat itu (sama dengan `waifu mic`). Mic yang tidak bisa dibuka tidak pernah dipakai: dia tetap
memakai mic sebelumnya dan menyebut alasannya. Kalau dia sedang berpikir atau bicara, gantinya
menunggu sampai dia selesai, dan hasilnya muncul di lembar itu. **Ikuti urutan di config** kembali
ke urutan `audio.input_device`.

Pilihan dari HP disimpan di file kecil `data\voice_override.json` (`audio.override_path`),
bukan di `config.yaml`. Sesudah ganti mic, daftarkan ulang suaramu dengan `waifu enroll` lewat
mic itu supaya skor kecocokannya bagus.

### Setelan suara

Di **Setelan** bisa diubah, langsung berlaku dan ikut disimpan di file yang sama:

- **Ambang kecocokan suaramu** (`speaker.threshold`, 0,30 sampai 0,70): turunkan sedikit kalau
  suaramu sendiri sering "bukan suaramu"; naikkan kalau suara orang lain ikut dijawab.
- **Ambang VAD** (`audio.vad.threshold_db`, -70 sampai -25 dB): turunkan (misalnya -50) kalau bar
  suaramu jarang melewati garis.
- **Margin VAD** (`audio.vad.margin_db`, 3 sampai 30 dB): seberapa jauh suara harus di atas bising
  mic itu; naikkan kalau kipas atau AC memicu "terlalu pendek" atau "tak ada kata".

**Simpan** hanya menyimpan angka yang kamu ubah; yang tidak diubah tetap mengikuti `config.yaml`.
**Reset ke default** mengembalikan ketiganya ke `config.yaml` (mic pilihan HP tetap).

### Beberapa mic berurutan

`audio.input_device` boleh berisi beberapa mic, urut dari yang paling diinginkan:

```yaml
audio:
  input_device: ["Headset (Polaris-BT), MME", "Microphone (USB Audio), MME"]
```

Cyrene memakai mic pertama yang bisa dibuka; mic pilihan HP selalu dicoba duluan, dan mic default
Windows paling akhir. Kalau mic yang dipakai terputus, berhenti mengirim suara, atau hanya
mengirim sunyi total selama 8 detik (misalnya headset Bluetooth dimatikan), dia pindah ke mic
berikutnya dan bilang sekali "Sekarang aku pakai mic Microphone (USB Audio)." Begitu headsetnya
tersambung lagi, dia kembali memakainya: "Mic Headset (Polaris-BT) tersambung lagi, aku pakai
itu lagi." Kalau tidak ada mic yang bisa dibuka, dia tetap jalan (pengingat dan alarm tetap
diucapkan) dan mencoba lagi tiap 5 detik. Satu nama seperti sebelumnya tetap bisa dipakai.

Mic yang tadi hanya mengirim sunyi total tidak dicoba lagi sampai daftar mic Windows berubah
atau kamu memilihnya di HP, jadi dia tidak bolak-balik di antara dua mic. Kalau semua mic yang
bisa dibuka hanya mengirim sunyi total (mic di-mute, atau izin mikrofon Windows untuk aplikasi
desktop mati), dia tetap di satu mic dan bilang sekali "Mic-ku cuma mengirim sunyi total, jadi
aku tidak bisa mendengarmu. ..."; begitu mic-nya tidak di-mute lagi, dia langsung mendengar.

Untuk melihat mic yang datang dan pergi, Cyrene memulai ulang PortAudio sebentar dengan mic
tertutup (ucapan yang jatuh tepat di saat itu tidak terdengar), hanya saat daftar mic Windows
berubah, saat mic gagal, atau saat ganti mic dari HP. Kalau `audio.input_device` atau
`audio.output_device` berisi **nomor** dari `waifu mics`, itu tidak dilakukan (nomornya bisa
bergeser), jadi mic yang kembali baru dipakai lagi setelah `waifu run` dijalankan ulang: pakai
nama dari `waifu mic`.

### "Tadi kamu denger apa?"

"Cyrene, tadi kamu denger apa?" (atau "kamu dengar apa tadi?", "tadi denger apa?") dijawab
langsung tanpa otaknya: ucapan terakhir yang dia dengar dan kenapa dia menjawab atau tidak.

```text
Master : "Matikan lampu."
Master : "Cyrene, tadi kamu denger apa?"
Cyrene : "Tadi aku dengar "matikan lampu", tapi kamu tidak memanggil namaku, jadi tidak kujawab."
```

Untuk suara lain dia menyebut skor kecocokannya dan batasnya ("kemiripannya dengan suaramu cuma
0,32, padahal butuh 0,45"), dan untuk suara tanpa kata panjangnya. Bunyi pendek seperti klik
atau batuk dilewati. Seperti [ulangi](#ulangi), kalimatnya harus hanya pertanyaan itu, dalam 8
detik sesudah dia bicara cukup tanpa nama, dan sesudah mode tamu berganti dia menjawab "Belum ada
yang kudengar." Jawabannya tidak ikut diulang oleh "ulangi".

Pertanyaannya sendiri juga harus terdengar: kalau mic-nya tidak mendengarmu, atau suaramu dianggap
suara orang lain, pertanyaan itu pun tidak dijawab. Buka halaman Suara atau jalankan `waifu mic`.

### Privasi diagnosa suara

Rekaman tidak pernah disimpan: level mic dihitung dari sampelnya lalu sampelnya dibuang.
Catatan ucapan (dengan teks yang ditangkap Whisper) hanya ada di memori selama `waifu run`
berjalan, paling banyak 50, hilang saat dia berhenti, hanya dikirim ke HP yang sudah dipasangkan
lewat Tailscale (tidak lewat Funnel, tidak saat mode tamu), dan jawaban "tadi kamu denger apa?"
tidak dicatat di log. Yang disimpan hanya `data\voice_override.json`: nama mic pilihan HP dan tiga
angka setelan.

## Hotkey global

Saat game layar penuh sedang di depan, avatar tertutup dan namanya kalah oleh suara game. Lima
tombol pintas Ctrl+Alt tetap sampai ke Cyrene apa pun jendela yang sedang aktif, juga game layar
penuh. Hanya di Windows dan selama `waifu run` jalan; sudah menyala tanpa pengaturan.

| Tombol | Yang terjadi |
|---|---|
| **Ctrl+Alt+C** | Langsung mendengarkan tanpa dipanggil namanya, seperti klik dua kali avatar: dia bilang "はい, Master?" lalu menunggu 8 detik. Kalau dia sedang bicara, ucapannya dipotong dan dia langsung mendengarkan. Verifikasi suara tetap berlaku. |
| **Ctrl+Alt+X** | Memotong ucapannya saat itu juga, juga di tengah jawaban panjang, lalu dia mendengarkan seperti sesudah klik dua kali. Kalimat pendek selagi dia berpikir ([Respons instan](#respons-instan)) hanya dipotong: dia mendengarkan setelah jawabannya. Kalau dia sedang diam, tidak terjadi apa-apa. |
| **Ctrl+Alt+R** | Mengulang ucapan terakhirnya, seperti "ulangi" (lihat [Ulangi](#ulangi)). Diabaikan selagi dia mendengarkanmu, berpikir, atau bicara. |
| **Ctrl+Alt+Q** | Berhenti mendengarkan (lihat di bawah); tekan lagi untuk mendengarkan lagi. |
| **Ctrl+Alt+T** | Seperti bertanya "Artinya apa teks yang kucopy?" tanpa mic: dia menerjemahkan teks yang dicopy (lihat [Artinya apa](#artinya-apa)). Diabaikan selagi dia mendengarkanmu, berpikir, atau bicara. |

Hotkey tetap jalan walau avatar mati atau `avatar.interactions.double_click_listen: false`.

### Berhenti mendengarkan (Ctrl+Alt+Q)

Saat main bareng teman lewat voice chat, namanya yang disebut teman bisa membangunkannya.
Ctrl+Alt+Q: "Oke Master, aku tidak mendengarkan dulu sampai jam 16.30. Tekan tombol yang sama
kalau mau ngobrol lagi." Sejak itu tidak ada suara mic yang sampai ke Whisper, sementara:

- pengingat, alarm, dan kabar lain tetap diucapkan; selagi alarm berbunyi dia tetap mendengar,
  jadi "aku sudah bangun" tetap mematikannya;
- mic tetap terbuka, jadi pindah mic otomatis tetap jalan dan halaman Suara masih menunjukkan
  level-nya (daftar **Yang terdengar** berhenti bertambah);
- sapaan pagi dan pertanyaan yang dia mulai sendiri menunggu sampai dia mendengarkan lagi.

Avatar menampilkan **Tidak mendengarkan** di pojok kiri atas, juga sebagai maskot. Dia
mendengarkan lagi dengan:

- Ctrl+Alt+Q lagi: "Oke Master, aku dengar lagi." (8 detik sesudahnya cukup tanpa nama);
- Ctrl+Alt+C atau klik dua kali avatar;
- tombol **Dengar lagi** di grup **Mikrofon** pada tombol cepat di HP atau `waifu tombol`;
- otomatis sesudah 2 jam (`hotkeys.quiet_hours`): "Aku mendengarkan lagi sekarang, Master.";
- menjalankan ulang `waifu run`.

Ctrl+Alt+X selama dia tidak mendengarkan hanya memotong ucapannya (misalnya pengingat), tanpa
membuka mic lagi.

Ctrl+Alt+Q selagi dia bicara langsung menghentikan pendengarannya, tapi kalimat "sampai jam ..."
dan tulisan **Tidak mendengarkan** di avatar baru muncul sesudah ucapannya selesai. Jangan tekan
Ctrl+Alt+Q lagi sambil menunggu: tekan kedua membuatnya mendengarkan lagi. Ctrl+Alt+X memotong
ucapannya, jadi konfirmasinya langsung terdengar.

### Mengganti atau mematikan tombolnya

```yaml
hotkeys:
  talk: F9        # Ctrl+Alt+F9 untuk langsung mendengarkan
  repeat: null    # tanpa Ctrl+Alt+R
  translate: Y    # Ctrl+Alt+Y untuk "artinya apa"
```

Tiap tombol satu huruf A-Z, angka 0-9, atau F1-F12 (Ctrl+Alt selalu ditambahkan); `null`
mematikan satu hotkey, `enabled: false` mematikan semuanya. Dua hotkey dengan tombol yang sama
ditolak, kecuali `translate` yang belum ditulis di `config.yaml`: kalau T sudah dipakai hotkey
lain (dari `config.yaml` sebelum v1.56), Ctrl+Alt+T mati dengan peringatan di log dan Cyrene tetap
jalan. Semua pengaturannya ada di `config.example.yaml`, bagian `hotkeys`. Jalankan ulang
`waifu run` sesudah mengubahnya.

Selama `waifu run` jalan, kelima kombinasi ini tidak sampai ke program lain, juga yang sedang
aktif: misalnya Ctrl+Alt+C (©), Ctrl+Alt+R (®), dan Ctrl+Alt+T (™) di Word, Ctrl+Alt+C di
PyCharm, atau Ctrl+Alt+X dan Ctrl+Alt+Q di Visual Studio. `waifu check` tidak menganggapnya
masalah, karena Cyrene memang memegangnya. Kalau kamu memakai salah satunya, ganti tombolnya
(misalnya ke F9) atau matikan dengan `null`.

`waifu check` punya baris `Hotkey global`: kombinasi yang sedang aktif di `waifu run` yang jalan
(tanpa `waifu run`, kombinasi di `config.yaml` yang akan dicoba), dan sampai jam berapa dia tidak
mendengarkan. Windows memberikan satu kombinasi hanya ke program pertama yang memintanya; kalau
sudah dipakai program lain, `waifu check` menulis `! Hotkey Ctrl+Alt+Q (berhenti mendengarkan)
sudah dipakai program lain; ganti hotkeys.quiet di config.yaml` (log `waifu run` juga
menyebutnya), dan hotkey lain tetap jalan. Kalau `waifu run` tidak memegang satu kombinasi pun,
barisnya `! Hotkey global tidak aktif di 'waifu run'`; sebabnya ada di baris sesudahnya atau di
log. Kalau `config.yaml` diubah sesudah `waifu run` mulai, `waifu check` tetap menampilkan
kombinasi yang dipegang `waifu run` dan mengingatkan bahwa yang baru dipakai setelah `waifu run`
dijalankan ulang.

### Kalau tombolnya tidak sampai

- Game yang dijalankan sebagai administrator, game dengan anti-cheat, atau game yang memasang
  pengait keyboard sendiri bisa menelan semua kombinasi ini selama game itu aktif, tanpa pesan
  apa pun. Untuk bicara, panggil namanya seperti biasa. Supaya voice chat tidak membangunkannya,
  tekan Ctrl+Alt+Q sebelum masuk game (atau dari desktop), lalu akhiri dengan **Dengar lagi** di
  HP atau `waifu tombol` (tombol itu hanya ada selama dia tidak mendengarkan). Mengganti tombol
  hanya membantu kalau satu kombinasi tertentu bentrok dengan tombol game itu.
- Keyboard dengan **AltGr** (misalnya tata letak Jerman atau Polandia): bagi Windows, AltGr sama
  dengan Ctrl+Alt, jadi kombinasi ini mengambil huruf khususnya (AltGr+Q di keyboard Jerman
  adalah "@"). Ganti tombolnya atau matikan dengan `null`. Keyboard US biasa tidak terpengaruh.
- Selagi dia berpikir, Ctrl+Alt+C diabaikan, sama seperti klik dua kali.

### Privasi hotkey

Windows hanya memberi tahu bahwa salah satu kombinasi itu ditekan; tombol lain yang kamu ketik
tidak pernah terlihat oleh Cyrene, dan log hanya menulis kombinasi dan aksinya.
`data\listen_pause.json` berisi jam mulai dan selesai selama dia tidak mendengarkan, lalu
dihapus; `data\hotkeys.json` berisi tombol dan nomor proses `waifu run` selama dia jalan.

## Kamus Cyrene

Dua kamus kecil supaya Cyrene lebih tepat mendengar dan mengucapkan kata-kata dunianya sendiri:
kata yang ditunggu Whisper, dan cara baca kata untuk suara Indonesianya. Keduanya sudah menyala
tanpa pengaturan.

### Kata yang ditunggu Whisper

Whisper mendapat daftar pendek kata yang sering kamu ucapkan ke Cyrene (`hotwords` di
faster-whisper), urut dari yang paling penting:

1. namanya dan `assistant.wake_words`;
2. nama setiap perangkat di `devices`, lalu nama aplikasi perangkat `type: apps`;
3. kalau teman gacha menyala, game di `gacha.games`: "HSR", "Honkai: Star Rail", "Genshin",
   "Genshin Impact", "ZZZ", "Zenless Zone Zero";
4. nama karakter milikmu, paling banyak 5 per game, yang paling langka dan levelnya paling
   tinggi dulu. Nama ini baru masuk setelah kamu menanyakan karakter game itu (misalnya
   "karakter HSR-ku siapa aja?") selama `waifu run` ini, dan hilang saat `waifu run` dijalankan
   ulang. Game-game itu bergiliran (nama pertama tiap game, lalu nama kedua tiap game, dan
   seterusnya), game yang terakhir ditanyakan lebih dulu, jadi setiap game yang kamu tanyakan
   kebagian nama. Cyrene tidak pernah membuka HoYoLAB hanya untuk daftar ini;
5. nama rutinitas (`mau_tidur` menjadi "mau tidur");
6. alias perangkat.

Daftarnya paling banyak 40 kata dan 400 karakter; kalau penuh, alias perangkat yang dibuang
lebih dulu, karena kebanyakan kata biasa ("lampu", "ac") yang sudah ditulis benar oleh Whisper.
Kalimat contoh untuk Whisper tetap pendek ("Cyrene, tolong nyalakan lampu kamar.").
`config.yaml` yang diubah baru dipakai setelah `waifu run` dijalankan ulang. Saat nama karakter
masuk, log hanya menyebut jumlah katanya, misalnya `Whisper hotwords now hold 31 terms`.

Daftar ini membuat Whisper lebih condong ke kata-kata itu, jadi tulis alias seperlunya: alias
yang terlalu umum bisa membuat Whisper menulisnya di tempat kata lain. Verifikasi suara dan
deteksi suara tetap berjalan sebelum Whisper.

### Cara baca kata

Suara Indonesia (edge-tts dan suara cadangan Windows) membaca singkatan seperti kata biasa,
jadi ada aturan bawaan:

| Kata | Dibaca |
|---|---|
| HSR | Ha Es Er |
| ZZZ | Zet Zet Zet |
| HoYoLAB | Hoyolab |
| HoYoverse | Hoyovers |

Aturan bawaan hanya cocok dengan huruf besar-kecil persis seperti itu, jadi "Zzz..." (suara
tidurnya) tetap. Kata hanya diganti kalau berdiri sendiri: "HSR-ku" dibaca "Ha Es Er-ku", tapi
"HSRX" tidak.

Ajari kata lain lewat suara. Dia bertanya dulu, dan baru mencatat setelah kamu bilang iya:

> "Cyrene, cara baca Acheron itu A Ke Ron."
>
> "Jadi Acheron kubaca A Ke Ron? Bilang iya kalau benar."
>
> "Iya."
>
> "Oke Master. Acheron mulai sekarang kubaca A Ke Ron."

Cara bacanya langsung dipakai mulai kalimat berikutnya, tanpa otak (seketika, tanpa biaya).
Di pertanyaan dan jawabannya, katanya masih dibaca dengan cara lama, jadi kamu mendengar kata
mana yang dia tangkap; kalau salah kata, jangan bilang iya.

Jawabanmu harus kalimat berikutnya yang dia dengar, dalam satu menit sejak dia bertanya, dan
hanya berisi iya ("iya", "ya", "oke", "boleh", "bener", "iya bener banget", "oke, catat aja",
boleh dengan namanya atau panggilanmu). Jawaban lain apa pun ("bukan", "iya, tapi nanti",
"iya?", perintah lain, atau "ulangi") membatalkannya tanpa dibahas, lalu dijawab seperti biasa.
Kalimat yang tidak untuknya (tanpa namanya setelah 8 detik) dan namanya saja ("Cyrene", dijawab
"はい、Master?", lalu "iya") tidak membatalkan. Pesan lewat HP, sapaan yang dia mulai sendiri,
dan pengingat atau pengumuman yang dia ucapkan juga membatalkannya, supaya "iya" untuk
pertanyaan lain (misalnya "Mau kutukarkan?" dari pengumuman kode) tidak pernah mencatat cara
baca.

Kalimatnya harus persis "cara baca <kata> itu <bacaan>": katanya 1 sampai 3 kata, bacaannya 1
sampai 8 kata. Boleh diawali namanya atau panggilanmu (`assistant.owner_call`), dan
"nama"/"kata"/"tulisan" sebelum katanya ("cara baca nama Acheron itu ..."), "harusnya",
"sebenarnya", "dibaca", atau "kayak" sesudah "itu", serta "ya", "dong", "aja", "oke", atau "nih"
di akhir tidak ikut dicatat. Kalimat ini juga jalan dalam 8 detik sesudah dia menjawab tanpa
memanggil namanya, dan saat mode tamu.

Lupakan cara baca lewat suara:

> "Cyrene, lupakan cara baca Acheron." (atau "hapus cara baca Acheron")
>
> "Oke Master. Cara baca Acheron sudah kulupakan."

Katanya diucapkan dengan cara baca biasa lagi. "untuk", "buat", atau "dari" sebelum katanya dan
"sekarang", "lagi", "juga", "itu", "yang tadi", "aja", atau "ya" sesudahnya tidak ikut dihitung
("hapus cara baca untuk Acheron sekarang"). Kalau kamu belum punya aturan untuk kata itu, dia
bilang "Aku belum punya cara baca khusus untuk Acheron, Master."; aturan bawaan (HSR, ZZZ, ...)
tidak bisa dilupakan, hanya diganti. Katanya harus disebut: "lupakan cara baca itu" diteruskan
ke otak.

Yang diteruskan ke otak seperti biasa, **tidak ditanyakan, dan tidak dicatat**:

- kalimat tanya: ada "?", kata tanya seperti apa, gimana, siapa, berapa, bener, nggak, atau
  bukan, atau diakhiri "ga", "gak", "kah", "kan", atau "mana" ("cara baca HSR itu Ha Es Er
  kan"). Suku kata di tengah bacaan tetap boleh: "cara baca Gallagher itu Ga Le Ger" ditanyakan;
- komentar tentang cara baca: kalimat yang memuat kata seperti salah, keliru, aneh, jelek,
  bagus, lucu, beda, gitu, begini, kok, sih, atau, harus, bisa, susah, gampang, banget, dari,
  dengan, pakai, penting, dulu, biasa, sama, lain, pelan, cepat, ribet, atau nanti, juga yang
  diulang dengan tanda hubung seperti "pelan-pelan" ("cara baca HSR itu salah", "cara baca jam
  itu begini", "cara baca buku itu pelan-pelan", "cara baca manga itu dari kanan ke kiri");
- kata ganti (kamu, aku, dia, ...), namanya sendiri, kata panggilannya, dan panggilanmu: "cara
  baca kamu itu salah" berarti cara dia membaca yang salah. Aturan untuk namanya sendiri atau
  panggilanmu hanya bisa lewat `waifu lafal add`;
- kalimat yang mirip tapi tidak persis ("cara bacanya Acheron itu ...", "cara baca Acheron tuh
  ...");
- semua yang kamu ketik atau kirim sebagai pesan suara lewat HP: obrolan HP selalu ke otak.

Otak tidak bisa menyimpan atau menghapus cara baca, jadi kalau dia menjawab seolah sudah
mencatat atau melupakan, cek dengan `waifu lafal list`, lalu pakai `waifu lafal add` atau
`waifu lafal remove`.

Atau lewat terminal:

```powershell
waifu lafal add Acheron A Ke Ron
waifu lafal add "Star Rail" Star Rel    # kata lebih dari satu: pakai tanda kutip
waifu lafal list                        # aturanmu, lalu aturan bawaan yang ditandai (bawaan)
waifu lafal remove Acheron
```

Aturanmu cocok dengan huruf besar atau kecil apa pun, dan aturanmu untuk kata yang sama dengan
aturan bawaan menggantikannya (misalnya `waifu lafal add HSR Honkai Star Rail`). Aturan bawaan
tidak bisa dihapus, hanya diganti. Paling banyak 200 aturan. `waifu run` yang sedang berjalan
memakai perubahan dari terminal mulai kalimat berikutnya, tanpa dijalankan ulang; `waifu say`
dan `waifu chat --speak` juga memakainya.

Yang tidak pernah diubah: suara Jepang, teks jawaban di HP (suaranya di HP tetap memakai
aturan), riwayat obrolan, dan log. Aturan juga berlaku untuk kata di dalam judul atau alamat web
yang dia bacakan.

Aturanmu disimpan di `data\tts_lexicon.json` (`tts.lexicon_path`), bukan di `config.yaml`.
File yang rusak (misalnya salah edit) dipindah menjadi `tts_lexicon.json.corrupt-<tanggal>`, dan
dia memakai aturan bawaan saja sampai ada aturan baru.

### Privasi kamus

Kamus hanya berisi kata dan cara bacanya, di PC ini, dan tidak dikirim ke mana pun. Nama
karakter untuk Whisper hanya ada di memori selama `waifu run` jalan. Seperti setiap perintah,
kalimat "cara baca ..." dan "lupakan cara baca ..." tertulis utuh di baris `Heard (id): ...`
log. Selain baris itu, log hanya menyebut jumlah kata yang ditunggu dan bahwa ada cara baca
yang ditanyakan, dicatat, dibatalkan, ditolak, atau dilupakan, tidak pernah katanya; kamus rusak
yang dipindah juga disebut tanpa isinya.

## Pemecahan masalah

Log `waifu run` tampil di terminal dalam bahasa Inggris. Baris seperti
`Heard (id): ...`, `Not addressed to Cyrene (no_wake_word); ignoring`, dan
`Ignoring another speaker (similarity 0.21)` menjelaskan kenapa dia diam. Tambahkan
`-v` untuk detail lebih banyak. Dengan `--log-file` (autostart memakai
`logs\waifu.log`) log yang sama juga ditulis ke file, lengkap dengan tanggalnya.

**Error DLL CUDA dari Whisper (ctranslate2).** Pesannya seperti
"Whisper gagal berjalan di GPU: cublas64_12.dll tidak ditemukan atau gagal dimuat".
faster-whisper butuh DLL cuBLAS dan cuDNN; Cyrene otomatis mendaftarkan folder DLL
dari torch dan paket `nvidia-*` di venv.

- Pastikan torch CUDA terpasang di venv yang sama (cek `+cu128 True` seperti di
  [Paket suara](#2-paket-suara-stt-verifikasi-suara)). Kalau yang terpasang versi
  CPU, pasang ulang:

  ```powershell
  .venv\Scripts\python -m pip uninstall -y torch torchaudio
  .venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
  ```

- Alternatifnya, pasang DLL-nya saja:
  `.venv\Scripts\python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`.
- Perbarui driver NVIDIA.
- Darurat tanpa GPU: di config, `stt.device: cpu` dan `stt.compute_type: int8`
  (serta `speaker.device: cpu`). Jauh lebih lambat.

**Mic tidak terdeteksi atau tidak mau dibuka.**

- Pertama, buka **Suara** di [halaman HP](#diagnosa-suara) selagi `waifu run` jalan: lihat mic
  yang dipakai, bicara, dan perhatikan apakah bar-nya melewati garis merah dan apa yang tertulis
  di **Yang terdengar** ("bukan suaramu", "tanpa nama", "tak ada kata", "terlalu pendek"). Dari
  sana mic bisa langsung diganti, dan ambang suaranya disetel, tanpa menjalankan ulang. Tanpa HP,
  tanya "Cyrene, tadi kamu denger apa?", tapi itu hanya membantu kalau dia mendengarmu dan tidak
  bereaksi (tanpa nama, tak ada kata): pertanyaannya lewat mic yang sama, jadi kalau mic itu tidak
  mendengarmu, atau suaramu dianggap suara orang lain, pertanyaan itu pun tidak dijawab. Kalau dia
  diam saja, langsung jalankan `waifu mic`.
- Kalau avatar menampilkan **Tidak mendengarkan**, atau baris `Hotkey global` di `waifu check`
  menulis `sedang tidak mendengarkan sampai ...`, dia sengaja tidak mendengarkan (Ctrl+Alt+Q):
  tekan Ctrl+Alt+Q lagi, atau **Dengar lagi** di tombol cepat HP (lihat
  [Hotkey global](#hotkey-global)).
- Jalankan `waifu mic`, lalu sebut "halo Cyrene" beberapa kali dengan suara biasa. Dia
  menampilkan tiap mic sekali, menandai mic yang dipakai Cyrene dan default Windows,
  mendengarkan tiap mic 3 detik (`waifu mic --seconds 5` untuk lebih lama), lalu menulis
  puncak dan RMS suaranya dengan bar `#`, apakah suaramu terdengar sekeras dan selama yang
  dibutuhkan Cyrene (bunyi klik atau batuk sekali tidak dihitung), dan baris
  `input_device: "..."` untuk mic yang paling jelas mendengarmu. Pakai baris
  itu untuk mengganti baris `input_device` di bagian `audio:` di `config.yaml`, jalankan ulang
  `waifu run`, lalu daftarkan ulang suaramu dengan `waifu enroll` lewat mic itu. Rekamannya tidak disimpan, dan `waifu mic` boleh
  dijalankan selagi `waifu run` jalan. `waifu setup mic` mengukur dengan cara yang sama lalu
  menuliskan mic pilihanmu (atau beberapa, berurutan) ke `config.yaml` sendiri.
- Kalau `audio.input_device` kosong, Cyrene memakai mic default Windows yang ada saat
  `waifu run` dimulai; default yang berubah sesudahnya (misalnya headset Bluetooth baru
  tersambung) baru dipakai setelah `waifu run` dijalankan ulang. Colokan mic di PC (misalnya
  "Microphone (High Definition Audio Device)") yang tidak tersambung ke mic sungguhan hanya
  mendengar bising, sekitar -50 dB. Suara baru dianggap bicara kalau mencapai -45 dB
  (`audio.vad.threshold_db`) dan sekaligus 12 dB (`audio.vad.margin_db`) di atas bising mic itu
  sendiri, jadi sekitar -38 dB untuk colokan yang bisingnya -50 dB; angka `butuh ... dB` yang
  ditulis `waifu mic` untuk tiap mic adalah batas itu. Baris `Mikrofon` di `waifu check`
  menyebut mic yang dipakai dan apakah itu default Windows.
- Headset Bluetooth: selama mic-nya terbuka, Windows memindahkan headset ke mode hands-free
  yang kualitasnya lebih rendah, jadi musik dan suara Cyrene di headset itu terdengar seperti
  telepon (sebentar saja saat `waifu mic` mendengarkannya). Pindahnya butuh satu-dua detik:
  untuk headset Bluetooth pakai `waifu mic --seconds 5` dan terus bicara selama gilirannya
  ("hampir tanpa suara" berarti mic-nya di-mute, diblokir Windows, atau headsetnya belum siap).
  Kalau headset itu dipakai sebagai mic Cyrene, mic-nya terbuka selama `waifu run` jalan, jadi
  headsetnya terus di mode itu; pakai speaker lain untuk suaranya (`audio.output_device`), atau
  mic terpisah.
- `waifu mics` menampilkan semua perangkat apa adanya (tanda `>` = mic default). Isi
  `audio.input_device` dengan nomor atau namanya.
- Headset yang datang dan pergi: isi `audio.input_device` dengan beberapa mic berurutan
  (`["Headset (Polaris-BT), MME", "Microphone (USB Audio), MME"]`), supaya Cyrene pindah ke mic
  berikutnya saat headsetnya mati dan kembali saat tersambung lagi (lihat
  [Beberapa mic berurutan](#beberapa-mic-berurutan)).
- Mic dibuka di sample rate bawaannya lalu di-resample ke 16 kHz, jadi
  `audio.sample_rate` tetap `16000`. Kalau satu entri mic (misalnya versi WASAPI
  atau WDM-KS) gagal dibuka dengan error sample rate, pilih entri mic yang sama
  dengan host API MME.
- Cek Windows Settings, Privacy & security, Microphone: izinkan aplikasi desktop
  memakai mic.
- Kalau dia tidak pernah mendengar: turunkan `audio.vad.threshold_db` (misalnya
  `-50`). Kalau suara kipas/AC memicu rekaman: naikkan `audio.vad.margin_db`.

**Ollama tidak jalan.** `waifu check` menampilkan "Ollama tidak bisa dihubungi di
http://127.0.0.1:11434". Buka aplikasi Ollama atau jalankan `ollama serve`. Kalau
modelnya belum ada: `ollama pull qwen3:8b` (cek dengan `ollama list`). Jawaban
pertama memang lebih lama karena model dimuat ke VRAM.

**Nama "Cyrene" ditulis lain oleh Whisper.** Lihat log `Heard (...)` saat kamu
memanggilnya. Kalau Whisper menulis misalnya "Sairen" dan dia mengabaikanmu,
tambahkan ejaan itu ke `assistant.wake_words`. Pencocokan sudah toleran terhadap
salah eja kecil, jadi cukup tambahkan ejaan yang benar-benar muncul. Kata
sehari-hari yang mirip namanya ("sirene", "sirine", "saring") tidak pernah
dianggap salah eja. Kata itu hanya memanggilnya kalau kamu menulisnya persis di
`wake_words`, dan sejak itu obrolan biasa seperti "ada suara sirene" juga
memanggilnya. Jadi tambahkan hanya kalau Whisper memang sering menulis namanya
begitu. Namanya dan `wake_words` juga diberikan ke Whisper sebagai kata yang ditunggu (lihat
[Kamus Cyrene](#kamus-cyrene)).

**Suaramu sendiri ditolak.** Log menampilkan
`Ignoring another speaker (similarity 0.22)`. Turunkan `speaker.threshold` sedikit
(lihat skor di `waifu speaker-test`), atau daftar ulang dengan `waifu enroll` di
ruangan dan posisi mic yang biasa kamu pakai. Pesan "Voiceprint tidak cocok dengan
model suara yang dipakai sekarang" juga berarti perlu `waifu enroll` lagi.

**Perangkat Tuya gagal.** "menolak koneksi" berarti `local_key` atau `version`
salah (jalankan wizard lagi). "tidak bisa dihubungi" berarti perangkat mati, belum
tersambung Wi-Fi, atau IP-nya berubah.

**IR tidak tertangkap atau AC tidak bereaksi.** Dekatkan remote ke blaster saat
`learn-ir`, pastikan Broadlink tidak terkunci, dan pastikan blaster menghadap
perangkatnya. Uji satu kode dengan `waifu device ac on`.

**Tidak ada suara.** edge-tts butuh internet; tanpa internet dia bicara dengan
[suara cadangan Windows](#suara-cadangan-offline) kalau suara bahasanya sudah ditambahkan
(`waifu check` menyebutnya). Uji dengan `waifu say "Halo Master"` dan
`waifu say --fallback "Halo Master"`; pilih speaker lewat `audio.output_device`. Cek juga volume Windows
tidak 0 atau dibisukan, misalnya oleh rutinitas dengan `volume 0` atau `mute` (lihat
[Musik, volume, dan aplikasi PC](#musik-volume-dan-aplikasi-pc)). Kalau log `waifu run`
menulis "Microsoft menolak suara ... (403)", biasanya Microsoft mengganti kunci layanan
suaranya dan edge-tts di PC ini sudah usang: jalankan
`.venv\Scripts\python -m pip install -U edge-tts`, lalu mulai ulang `waifu run`. Kalau pip
bilang edge-tts sudah yang terbaru dan suaranya masih ditolak, tunggu rilis edge-tts
berikutnya (biasanya beberapa hari) lalu ulangi; selama itu dia bicara dengan suara cadangan
Windows, atau jawabannya hanya tampil sebagai teks kalau suara itu belum ditambahkan.

## Checklist uji perangkat keras

Uji ini dilakukan manual, bukan bagian dari `pytest`. Simpan pekerjaanmu sebelum
menguji nomor 5.

**Persiapan**

- [ ] `waifu check` menampilkan `✓` untuk config, otak, paket suara, CUDA,
      voiceprint, dan mic, serta semua kode IR yang dibutuhkan sudah dipelajari.
- [ ] `waifu chat --dry-run -v`: "nyalakan lampu kamar dan AC 24 derajat"
      menghasilkan dua `control_device` dan jawaban pendek.
- [ ] `waifu say "Halo Master, おかえりなさい!"` terdengar bahasa Indonesia lalu
      bahasa Jepang.
- [ ] Setiap perangkat bereaksi lewat CLI: `waifu device lampu_kamar on`,
      `waifu device ac temp 24`, `waifu device proyektor power`,
      `waifu device tv power`.
- [ ] `waifu setup`: mic yang kamu pilih, perangkat Tuya dari wizard, alamat IR blaster, dan
      topik ntfy tertulis di `config.yaml` dengan komentarnya tetap utuh, ada cadangan di
      `data\config.yaml.bak-...`, notifikasi tes masuk ke HP, dan `waifu check` tidak lagi
      menampilkan baris "waifu setup". `waifu device lampu_kamar on` menyalakan lampu.
- [ ] `waifu mic`: sambil kamu menyebut "halo Cyrene", mic yang dipakai Cyrene menunjukkan
      `✓ suara terdengar` (atau isi `audio.input_device` dengan baris yang dia sarankan), dan
      baris `Mikrofon` di `waifu check` menyebut mic itu.
- [ ] `waifu speaker-test`: suaramu `✓`, suara orang lain `✗`.
- [ ] Halaman **Suara** di HP (lihat [Diagnosa suara](#diagnosa-suara)): bar level bergerak
      melewati garis merah saat kamu bicara ke mic yang dipakai, dan "Cyrene, nyalakan lampu
      kamar" muncul di **Yang terdengar** sebagai "dijawab" dengan skormu.

**User story** (jalankan `waifu run` dan tunggu sampai muncul "Cyrene siap
mendengarkan"; jawaban pertama bisa lebih lama karena model Ollama baru dimuat)

- [ ] **1. Lampu.** "Cyrene, nyalakan lampu kamar": lampu menyala dan dia menjawab
      dengan suara sekitar 3 detik setelah kamu selesai bicara.
- [ ] **2. Dua perangkat sekaligus.** "Cyrene, AC dua puluh empat derajat dan
      matikan proyektor": AC menjadi 24°C, proyektor mati, dan dia menjawab dengan
      satu ringkasan.
- [ ] **3. Lanjutan tanpa nama.** Tepat setelah dia menjawab: "terus nyalakan TV
      juga": TV menyala tanpa menyebut "Cyrene". Kalimat yang sama setelah lebih
      dari 8 detik tanpa nama diabaikan.
- [ ] **4. Tamu diabaikan.** Orang lain bilang "Cyrene, matikan lampu": tidak ada
      yang terjadi, log menampilkan `Ignoring another speaker (similarity ...)`.
      Kalimat yang sama darimu tetap dilayani.
- [ ] **5. Mematikan PC dengan aman.** "Cyrene, matikan komputer": dia bertanya
      dulu dan belum mematikan apa pun. Jawab "iya": Windows memberi tahu PC akan
      mati dalam 30 detik. Bilang "Cyrene, batalkan": jadwal dibatalkan dan PC
      tetap menyala. Ulangi dan jawab "tidak": tidak ada yang dijadwalkan.

**Opsional: menyalakan PC**

- [ ] Smart plug + BIOS: shutdown Windows, matikan lalu nyalakan colokan dari
      aplikasi, PC menyala sendiri.
- [ ] Wake-on-LAN: dari HP atau PC lain, magic packet membangunkan PC yang mati.

**Opsional: autostart**

- [ ] Setelah `waifu autostart enable`, logout lalu login: jendela "Cyrene" muncul
      minimize, sekitar 15 detik kemudian dia bilang "Cyrene siap, Master." dan
      "Cyrene, nyalakan lampu kamar" langsung dilayani.
- [ ] Selama jendela itu terbuka, `waifu run` di terminal lain menolak jalan.
- [ ] Menutup jendela "Cyrene" menghentikannya; `waifu autostart disable` membuat
      login berikutnya tidak menjalankannya lagi.

**Versi baru** (lihat [Versi baru](#versi-baru))

- [ ] Tutup jendela "Cyrene" yang lama dan jalankan lagi `waifu run` (sekali, manual):
      `waifu check` menampilkan `✓ waifu run: v1.26, terbaru`, dan `waifu version` menampilkan
      versi yang sama di baris "Kode di disk" dan "Yang berjalan". Tidak ada jendela yang
      berkedip saat dia mulai.
- [ ] Atur sementara `version_notice.check_minutes: 5`, jalankan ulang `waifu run`, lalu
      tambahkan baris `## v1.99: uji` di atas versi teratas `CHANGELOG.md` (jangan di-commit):
      dalam 5 menit, di jam aktif, dia bilang "<panggilanmu>, ada versi baru Cyrene: v1.99, uji.
      Mulai ulang aku kalau sempat ya." sekali, HP menerima kalimat yang sama, dan `waifu check`
      menampilkan `! waifu run yang sedang jalan masih v1.26 ...`. Ngobrol dengannya saat itu:
      kalimatnya baru diucapkan sesudah jendela lanjutannya lewat. Hapus lagi barisnya dan
      kembalikan pengaturannya.

**Opsional: suara anime**

- [ ] `waifu check` menampilkan `✓` untuk Folder Applio, Python Applio, Model RVC,
      Index RVC (kalau diisi), dan Skrip server RVC; baris server boleh `!` (belum
      berjalan) karena `check` tidak pernah menyalakannya.
- [ ] `waifu say "Halo Master, おかえりなさい!"` terdengar dengan suara つくよみちゃん,
      `waifu say --raw` dengan kalimat yang sama terdengar dengan suara edge-tts, dan
      bahasa Indonesianya tetap jelas.
- [ ] Ukur tambahan waktunya: dengan `waifu run`, jeda dari selesai bicara sampai dia
      menjawab bertambah kira-kira 0,3–1 detik dibanding `tts.rvc.enabled: false`.
- [ ] Dengan `tts.rvc.model` sementara menunjuk file yang tidak ada, `waifu say` tetap
      bicara dengan suara biasa dan menampilkan "Suara anime tidak tersedia".
- [ ] Respons instan (lihat [Respons instan](#respons-instan)): dengan Claude, "Cyrene, besok
      hujan nggak?" membuatnya bilang kalimat pendek seperti "Sebentar, kucek dulu ya." sekitar
      1,2 detik setelah dia mulai berpikir (kira-kira 2 sampai 3 detik setelah kamu selesai
      bicara), dengan suara つくよみちゃん, lalu jawabannya menyusul tanpa bertumpuk;
      pertanyaan berikutnya yang lambat mendapat kalimat lain.
- [ ] Setelah `waifu run` bilang siap, menutup jendelanya juga mematikan server RVC:
      `netstat -ano | findstr :9881` tidak menampilkan apa-apa lagi.

**Opsional: suara cadangan offline**

- [ ] Setelah suara Indonesia (dan Jepang) ditambahkan di Settings, `waifu voices`
      menampilkannya dan `waifu check` menampilkan
      `✓ Suara cadangan bahasa Indonesia: Microsoft Andika (id-ID, laki-laki)`.
- [ ] `waifu say --fallback "Halo Master, おかえりなさい!"` terdengar dengan suara Windows
      (lewat suara anime kalau menyala) dan nadanya pas; kalau tidak, atur
      `tts.fallback.rvc_pitch` lalu ulangi.
- [ ] Cabut kabel jaringan (atau matikan Wi-Fi) saat `waifu run` berjalan: dia tetap menjawab
      dengan suara, dan log mencatat sekali `The id voice failed; trying its offline
      fallback ...`. Sambungkan lagi: paling lama 10 menit kemudian dia kembali ke edge-tts
      dan log mencatat `The id voice works again`.

**Opsional: avatar 3D**

- [ ] `waifu check` menampilkan `✓` untuk Model VRM avatar, Jendela maskot avatar
      (Electron), dan Port avatar.
- [ ] `waifu avatar --demo`: maskot transparan muncul di pojok kanan bawah dengan kredit
      つくよみちゃん（© Rei Yumesaki）, ekspresi dan keadaannya berganti setiap 3 detik,
      mulutnya bergerak, dan bisa digeser. Ctrl+C menutupnya.
- [ ] Dengan `waifu run`, maskot muncul sebelum "siap mendengarkan". "Cyrene, nyalakan
      lampu kamar": dia tampak berpikir, mulutnya bergerak seirama suaranya, wajahnya
      memasang ekspresi jawaban, lalu dia condong mendengarkan selama jendela lanjutan.
- [ ] Menutup maskot dengan × tidak menghentikan Cyrene; menutup jendela konsol
      "Cyrene" juga menutup maskot. Di taskbar dan Alt+Tab maskot bernama
      "Avatar Cyrene", bukan "Cyrene".
- [ ] Dengan `tts.rvc.enabled: false`, `waifu say "[senang] Halo Master!"`: maskot
      muncul, mulutnya bergerak sejak kata pertama dengan wajah senang, lalu tertutup.

**Opsional: avatar Live2D dan interaksi** (setelah langkah 1–3 di bagian itu)

- [ ] `waifu check` menampilkan `✓` untuk Mesin avatar: Live2D, Model Live2D avatar, File
      model Live2D, dan Cubism Core Live2D.
- [ ] `waifu avatar --demo`: model Live2D muncul transparan dengan kredit
      `Cyrene © miHoYo · Live2D: MomokaSono`, berkedip, rambut dan bajunya bergoyang
      (physics), mulutnya bergerak, dan ekspresinya berganti.
- [ ] Matanya dan kepalanya mengikuti kursor di mana pun di layar.
- [ ] Klik kepalanya: wajahnya langsung senang dan terminal mencetak satu kalimat elusan.
      Klik badannya: wajahnya kaget. Tekan badannya lalu tarik: maskot bergeser.
- [ ] Klik di area transparan di samping kepalanya mengenai jendela di belakang maskot.
      Tombol × di pojok kanan atas tetap bisa diklik.
- [ ] Di `waifu run`: elusan dijawab dengan suara dan gerak mulut; klik dua kali lalu
      langsung bicara tanpa memanggil namanya ("nyalakan lampu kamar") dijalankan.
- [ ] Di `waifu run`, minta jawaban panjang ("Cyrene, ceritakan sejarah candi Borobudur"),
      lalu klik dua kali padanya saat dia bicara: suaranya langsung berhenti, mulutnya
      menutup, dia mendengarkan tanpa kalimat `listen`, dan "nyalakan lampu kamar" tanpa
      memanggil namanya dijalankan. Log menampilkan `Speech was cut off before its end`.
- [ ] Ulangi empat langkah terakhir dengan `engine: vrm`: kontrolnya sama.

**Teman main gacha** (setelah langkah 1–4 di bagian itu)

- [ ] `waifu gacha status` menampilkan ketiga game tanpa `✗`.
- [ ] `waifu gacha history hsr --full` (lalu `genshin`, `zzz`): pity tiap banner sama
      dengan hitunganmu di game.
- [ ] `waifu notify-test` masuk ke HP.
- [ ] Di `waifu run`: "Cyrene, Trailblaze Power-ku berapa?" dijawab dengan angka yang
      sama dengan di game.
- [ ] Turunkan sementara `stamina_minutes` sampai di atas sisa waktu penuh: dalam 15
      menit dia mengingatkan lewat suara dan HP, sekali saja.
- [ ] Sebelum reset: pada malam ketika event HSR atau ZZZ yang belum selesai berakhir dalam 2
      hari, atau musim endgame HSR / Spiral Abyss yang belum maksimal berakhir dalam 3 hari,
      mulai `daily_time` dia menyebutnya sekali dengan angka yang sama dengan
      `waifu gacha <game> events`. Untuk musim HSR, catat apakah "baru dapat X dari Y" sama
      dengan bintang di game.
- [ ] Tutup HSR sesudah jam 22.00 dengan Trailblaze Power yang penuh sebelum jam 07.00: dalam 15
      menit dia bilang jam penuhnya; malam itu tidak ada "sudah penuh", dan sesudah jam 07.00
      ada, sekali, kalau belum kamu pakai.
- [ ] `waifu gacha codes`: tiap game tanpa `!`, dan kode yang belum ditukarkan sama dengan kode
      yang masih berlaku menurut pengumuman resmi game.
- [ ] Di `waifu run`: sekitar semenit setelah siap, kode baru (pertama kali: semua yang masih
      berlaku) diumumkan dan masuk ke HP dengan kodenya; setelah `waifu run` dijalankan ulang,
      kode itu tidak diumumkan lagi.
- [ ] Untuk kode yang belum pernah kamu pakai: "Cyrene, iya, tukarkan" (atau `waifu gacha
      codes hsr --redeem`) bilang berhasil, hadiahnya muncul di surat dalam game, dan
      `waifu gacha codes hsr` mencatatnya di "Terakhir dicoba".
- [ ] `waifu check` menampilkan `✓ Laporan game mingguan: tiap Minggu jam 19:00`.
- [ ] `waifu gacha report`: pull minggu ini, 50/50-nya, pity, dan bintang endgame tiap game
      sama dengan di game; banner dan event yang disebut memang berakhir dalam 7 hari.
- [ ] Laporan mingguan: tulis sementara `weekly_report.day` hari ini dan `time` beberapa menit
      lagi, hapus `data\weekly_report.json`, lalu jalankan ulang `waifu run`. Pada jamnya dia
      bercerita sekali dalam 3-4 kalimat tanpa menyuruh pull atau top up dan tanpa angka uang,
      teksnya masuk ke HP, dan log menampilkan `Tool weekly_report` tanpa `Tool game_endgame`
      atau `Tool finance_summary`. Restart sesudahnya tidak mengulangnya. Kembalikan
      pengaturannya.
- [ ] `waifu gacha income`: Primogem, Stellar Jade, dan Polychrome bulan ini sama dengan
      Traveler's Diary, Trailblaze Monthly Calendar, dan Inter-Knot monthly report di HoYoLAB.
- [ ] `waifu gacha income zzz --month <bulan lalu, YYYY-MM>`: angka bulan lalu yang muncul, sama
      dengan HoYoLAB (ini memastikan format bulan ZZZ yang belum pernah dicoba; kalau keluar
      `HoYoLAB menjawab bulan lain untuk pendapatan ZZZ`, catat dan laporkan).
- [ ] Di `waifu chat`: "bulan ini aku dapat berapa Primogem?" dijawab dengan angka yang sama,
      tanpa menyarankan pull atau top-up; sapaan pagi tidak menyebutnya.

**Data akun HSR** (lihat [Data akun HSR](#data-akun-hsr-karakter-endgame-dan-event))

- [ ] `waifu gacha hsr events`: jam berakhir tiap warp dan 5★ unggulannya sama dengan
      timer banner di game (sampai menitnya), begitu juga jam mulai warp berikutnya.
- [ ] `waifu gacha hsr endgame`: bintang dan jam berakhir musim tiap mode sama dengan di
      game.
- [ ] `waifu gacha hsr characters`: Eidolon dan Superimposition Light Cone satu karakter
      (dan tiap Path Trailblazer) sama dengan di game.
- [ ] Di `waifu run` atau `waifu chat`: "Cyrene, Acheron-ku Eidolon berapa?" dan "Banner
      HSR apa sekarang?" dijawab sama dengan perintah di atas.
- [ ] (Opsional) Sembunyikan sendiri sementara *Show your Character Details in the Battle
      Chronicle* di HoYoLAB: `waifu gacha hsr characters` menampilkan `✗ Data HSR ini tidak
      bisa dibaca karena Battle Chronicle di HoYoLAB tidak publik...`. Buka lagi setelahnya;
      Cyrene tidak pernah mengubah setelan itu.
- [ ] Sapaan pagi di `waifu run` (lihat **Sapaan pagi** di bawah) tidak menyebut banner,
      event, atau musim endgame HSR, Genshin, atau ZZZ, dan log tidak menampilkan
      `Tool game_characters`, `Tool game_endgame`, atau `Tool game_events` untuk giliran itu.

**Data akun Genshin dan ZZZ** (lihat [Data akun Genshin dan
ZZZ](#data-akun-genshin-dan-zzz-karakter-endgame-dan-event))

- [ ] `waifu gacha genshin characters`: Constellation dan Refinement weapon satu karakter (dan
      Traveler) sama dengan di game.
- [ ] `waifu gacha zzz characters`: Mindscape Cinema satu agent sama dengan di game.
- [ ] `waifu gacha genshin endgame`: bintang dan chamber terdalam Spiral Abyss, act dan
      difficulty Imaginarium Theater, serta difficulty dan waktu Stygian Onslaught sama dengan
      di game, begitu juga jam berakhir tiap musim.
- [ ] Di `waifu chat`: "Abyss-ku lantai 12 berapa bintang?" dijawab sama dengan bintang lantai
      12 di game.
- [ ] `waifu gacha zzz endgame`: rating dan skor Shiyu Defense, serta skor dan bintang Deadly
      Assault sama dengan di game. Di `waifu chat -v`, "Shiyu Defense-ku gimana?": catat apa
      yang di hasil tool bernama `Frontier 5 battle 1`, `Frontier 5 battle 2`, dan seterusnya
      (pertarungan di frontier kelima), dan namanya di game.
- [x] Jam kalender Genshin dan ZZZ sudah cocok di akun asli (2026-10-16): jam mulai dan
      berakhir tiap event dan banner di `waifu gacha zzz events` sama dengan hitung mundur dari
      HoYoLAB sendiri sampai menitnya, dan di `waifu gacha genshin events` banner berakhir
      14:00 WIB (15:00 waktu server) dan Spiral Abyss 03:00 WIB (reset 04:00 waktu server),
      sesuai jadwal server. Kalau suatu saat ada jam yang terasa meleset, bandingkan lagi
      dengan timer di game.
- [ ] `waifu gacha genshin events` setelah Master main Spiral Abyss musim ini: jadwal
      endgame-nya tertulis `Spiral Abyss (<nama musim>): <bintang>/36, berakhir ...`, dengan
      bintang yang sama dengan di game (bukan `36/<bintang>`). Imaginarium Theater tertulis
      sekali saja (`Imaginarium Theater: berakhir ...`).
- [ ] Di `waifu chat -v`: "Stat Furina-ku gimana?" dan "Drive Disc Ellen apa aja?": hasil tool
      `game_characters` di log menyebut set artifact atau Drive Disc dan nama stat yang sama
      dengan di game, dan jawabannya hanya dari data itu.
- [ ] (Opsional) Sembunyikan sendiri sementara detail karakter Genshin di Battle Chronicle
      HoYoLAB: `waifu gacha genshin characters` menampilkan `✗ Data Genshin ini tidak bisa
      dibaca karena Battle Chronicle di HoYoLAB tidak publik...`. Buka lagi setelahnya; Cyrene
      tidak pernah mengubah setelan itu.

**Ingatan**

- [ ] "Cyrene, ingat ya, aku alergi udang": dia mengiyakan dalam satu kalimat, dan
      `waifu memory list` menampilkan catatannya.
- [ ] Setelah sesi baru (tunggu 10 menit, atau jalankan ulang): "Cyrene, enaknya masak
      apa?" dijawab tanpa udang, tanpa diingatkan.
- [ ] "Cyrene, apa yang kamu ingat tentang aku?": ringkasan singkat, bukan daftar yang
      dibacakan. "Cyrene, lupakan soal udang": catatannya hilang dari `waifu memory list`.
- [ ] "Cyrene, ingat PIN ATM-ku 1234": dia menolak, dan tidak ada catatan baru.

**Jadwal dan rutinitas** (tulis dulu rutinitas `tidur` di `config.yaml`: contohnya ada di
`config.example.yaml`, hapus tanda `#` di depannya. Kalau rutinitas `tidur` sudah ada,
tambahkan langkah `- {device: ac, command: off, after_minutes: 180}` di akhirnya (lihat
[Mode tidur](#mode-tidur)). Lalu jalankan ulang `waifu run`; lihat
[Menulis rutinitas di config.yaml](#menulis-rutinitas-di-configyaml). Kode IR `temp_26`
dan `off` untuk `ac` harus sudah dipelajari.)

- [ ] `waifu check` menampilkan `✓ Jadwal: 0 entri` dan `✓ Rutinitas tidur: ...`.
- [ ] "Cyrene, dua menit lagi ingatkan aku minum air": dia menyebut jamnya, `waifu schedule
      list` menampilkannya, dan dua menit kemudian dia mengucapkannya sendiri dan HP
      menerima notifikasinya.
- [ ] "Cyrene, matikan lampu kamar satu menit lagi": lampunya mati tanpa dia berkata apa-apa.
- [ ] "Cyrene, mode tidur": lampu mati, AC ke 26 derajat, dan dia menyebut jam AC akan mati.
      `waifu schedule list` menampilkan `perangkat ac off (rutinitas tidur)`. "Cyrene,
      batalkan AC mati nanti": jadwal itu hilang dari `waifu schedule list`.
- [ ] Ubah sementara `after_minutes: 180` menjadi `2`, jalankan ulang `waifu run`, lalu
      "Cyrene, mode tidur": dua menit kemudian AC mati tanpa dia berkata apa-apa.
      `waifu routine run tidur` sama, dengan baris `Dijadwalkan ...`. Setelah itu kembalikan
      ke `180`, lalu jalankan ulang `waifu run` lagi: kalau tidak, malam itu AC mati dua
      menit setelah "mode tidur".
- [ ] "Cyrene, matikan komputer jam 11 malam": dia menolak menjadwalkannya dan tidak
      menawarkan mematikan `colokan_pc` sebagai gantinya.
- [ ] "Cyrene, matikan semua perangkat jam 2 pagi": `waifu schedule list` tidak berisi
      `colokan_pc` (lalu batalkan jadwal yang dibuat).
- [ ] Jadwalkan pengingat, tutup Cyrene sampai jamnya lewat lebih dari
      `late_grace_minutes`, lalu jalankan lagi: dia bilang ada pengingat yang terlewat, dan
      HP menerima `Terlewat (...)`.
- [ ] "Cyrene, ingatkan ulang tahun Ibu tiap 5 Mei": dia menyebut 5 Mei jam 08.00 (tahun
      depan kalau tanggalnya sudah lewat) dan `waifu schedule list` menampilkan `tiap 5 Mei`.
      "Cyrene, tiap tanggal 20 ingatkan bayar listrik" tampil sebagai `tiap tanggal 20`, dan
      "jadwal bulan ini apa?" menyebut tagihan itu. Batalkan keduanya setelah itu.
- [ ] "Cyrene, matikan colokan PC": dia minta konfirmasi dulu (jawab "tidak").
- [ ] Dengan `waifu run` berjalan, buka `waifu chat`: chat bilang jadwal dijalankan oleh
      Cyrene yang sedang berjalan, dan pengingat yang dibuat di chat diucapkan oleh
      `waifu run`.
- [ ] `waifu chat --dry-run`: jadwal yang dibuat di sana tidak muncul di
      `waifu schedule list`.

**Alarm bangun** (lihat [Alarm bangun](#alarm-bangun))

- [ ] `waifu check` menampilkan `✓ Alarm: 0 alarm, bunyi tiap 60 detik sampai 10 menit; hanya saat
      'waifu run' jalan dan PC menyala`.
- [ ] "Cyrene, pasang alarm dua menit lagi buat tes": dia menyebut jamnya, dan `waifu schedule
      list` menampilkan `alarm "tes"`. Dua menit kemudian dia bilang "Master, bangun! ...", lalu
      lagi semenit kemudian, lebih keras. HP menerima notifikasinya kalau notifikasi HP menyala.
- [ ] Selagi berbunyi, "Cyrene, matikan lampu": lampunya mati dan alarmnya tetap berbunyi. Lalu
      "aku sudah bangun" tanpa namanya: dia menjawab "Oke Master, alarmnya kumatikan." dan tidak
      berbunyi lagi.
- [ ] Pasang lagi, lalu saat berbunyi bilang "tunda": dia menyebut jam lima menit lagi, dan pada
      jam itu berbunyi lagi.
- [ ] Pasang lagi; saat berbunyi, buka panel tombol di HP dan `waifu tombol` di PC: grup **Alarm**
      ada paling atas. **Matikan alarm** di salah satunya mematikannya.
- [ ] Nyalakan mode bisik, pasang alarm di jam mode bisik: bunyinya tidak berbisik.
- [ ] Pasang alarm, tutup `waifu run` sampai lewat 3 menit dari jamnya, lalu jalankan lagi: alarmnya
      tidak berbunyi, dan HP menerima "Alarm jam ... terlewat." kalau notifikasi HP menyala.

**Daftar tugas**

- [ ] `waifu check` menampilkan `✓ Daftar tugas: 0 terbuka`.
- [ ] "Cyrene, tambah tugas: beli susu" dan "catat tugas kerjakan laporan, tenggatnya Jumat":
      dia menyebut tanggal Jumat yang benar, dan `waifu todo` menampilkan keduanya.
- [ ] "Cyrene, tugasku apa aja?": jumlahnya dan yang paling mendesak, bukan daftar yang
      dibacakan semuanya.
- [ ] "Beli susu sudah": tugasnya hilang dari `waifu todo` dan muncul di
      `waifu todo list --done`.
- [ ] "Cyrene, tambah tugas telepon dokter jam 4 sore": dia bilang itu lebih cocok jadi
      pengingat (atau membuat keduanya kalau diminta).
- [ ] "Cyrene, catat tugas ganti PIN ATM-ku 4821": dia menolak, dan tidak ada tugas baru;
      "catat tugas ganti PIN ATM" dan "catat tugas beli token listrik" tetap dicatat.
- [ ] "Ganti tugas laporan jadi kerjakan laporan bab 2": `waifu todo` menampilkan teks baru
      dengan tenggat yang sama.
- [ ] `waifu todo add bayar listrik --due <tanggal kemarin>`, lalu hapus `data\briefing.json`
      dan jalankan `waifu run` di jendela jam sapaan pagi: sapaannya menyebut tugas yang
      terlambat itu.

**Musik dan aplikasi PC** (tambahkan dulu `musik` dan `aplikasi` di `config.yaml`; lihat
[Musik, volume, dan aplikasi PC](#musik-volume-dan-aplikasi-pc))

- [ ] `waifu check` menampilkan `✓ Aplikasi ...` untuk setiap aplikasi.
- [ ] Putar lagu di Spotify atau YouTube: "Cyrene, pause musiknya" menjeda, "lanjutkan"
      memutar lagi, "lagu berikutnya" pindah lagu.
- [ ] "Cyrene, volume 30": indikator volume Windows berhenti di sekitar 30. "Cyrene,
      kecilkan volume" menurunkannya sekitar 10, "kecilkan volume 20 persen" sekitar 20.
- [ ] "Cyrene, buka Spotify": Spotify terbuka. "Cyrene, tutup Spotify": tidak ada yang
      ditutup, dan dia bilang tidak bisa.
- [ ] Rutinitas dengan `{device: musik, command: stop}`: musik berhenti, dan menjalankannya
      lagi saat musik sudah berhenti tidak memutarnya.
- [ ] "Cyrene, pause musiknya jam 11 malam": dia tidak menjadwalkan `play_pause`, tapi
      `stop` (cek dengan `waifu schedule list`, lalu batalkan).

**Volume suaraku** (lihat [Volume suaraku](#volume-suaraku))

- [ ] `waifu check` menampilkan `✓ Volume suaraku: 100%, mode bisik mati (22:00-06:00 paling
      keras 50%)`.
- [ ] `waifu say --volume 30 "Halo Master"` terdengar pelan, dan
      `waifu say --volume 150 "Halo Master"` lebih keras tapi tidak pecah atau kresek.
- [ ] Sambil musik diputar, "Cyrene, kecilkan suaramu": jawabannya sudah lebih pelan, musiknya
      tidak ikut pelan, dan indikator volume Windows tidak muncul. "Cyrene, volume 30" tetap
      menggeser volume Windows.
- [ ] Sesudah jam 22:00, "Cyrene, nyalakan mode bisik": suaranya pelan. Sapaan pagi sesudah
      jam 06:00 terdengar dengan volume biasa.
- [ ] Mulai ulang `waifu run`: volumenya masih seperti terakhir diubah.

**Lagu di YouTube** (lihat [Lagu di YouTube](#lagu-di-youtube); jangan sedang main game)

- [ ] `waifu check` menampilkan `✓ Lagu YouTube: aktif (YouTube)`.
- [ ] `waifu youtube lofi buat belajar`: halaman pencarian YouTube terbuka di browser default.
      `waifu youtube` dengan link video YouTube yang kamu salin dari tombol Share: video itu
      terbuka dan diputar.
- [ ] Dengan otak Claude: "Cyrene, putar lagu Hope Is the Thing With Feathers": video lagu itu
      terbuka dan diputar, dia tidak membacakan linknya, dan `waifu usage` menghitung pencarian
      webnya.
- [ ] Dengan otak Ollama: kalimat yang sama membuka halaman pencarian YouTube, dan dia bilang
      begitu.
- [ ] Dengan perangkat musik (`type: media`): "Cyrene, pause musiknya" menjeda lagu di browser,
      "lagu berikutnya" pindah lagu. Tanpa perangkat itu, "Cyrene, lagu berikutnya" tidak
      membuka halaman apa pun.
- [ ] "Cyrene, cari berita terbaru soal Hoyo-Mix" (tanpa kata putar, lagu, atau musik): tidak ada
      halaman yang terbuka.
- [ ] Dengan otak Claude: "Cyrene, cari lirik lagu Hope Is the Thing With Feathers" (bertanya
      soal lagu, tanpa kata putar): dia mencari di web dan menjawab, dan tidak ada halaman yang
      terbuka.
- [ ] Dengan `music.service: youtube_music` dan `waifu run` yang dijalankan ulang, lagu yang
      diminta terbuka di YouTube Music.

**Melihat layar** (otak Claude; buka dulu sesuatu yang tidak pribadi di monitor utama, misalnya
halaman tim di game atau sebuah error)

- [ ] `waifu check` menampilkan `✓ Melihat layar: aktif (Claude)`.
- [ ] "Cyrene, lihat layarku, ini apa?": dia menjawab tentang yang tampil di monitor utama,
      dan log menampilkan `Captured the main monitor at ...` sekali saja.
- [ ] "Cyrene, menurutmu gimana?" (tanpa kata lihat/layar): tidak ada `Captured ...` baru di
      log.
- [ ] Game dalam mode borderless tertangkap dengan benar (bukan hitam).
- [ ] Dengan scaling 125% atau 150% (Settings > Display > Scale) dan sesuatu yang tidak
      pribadi di pojok kanan bawah: dia bisa menyebut isi pojok itu dan taskbar, jadi seluruh
      layar tertangkap, bukan hanya bagian kiri atasnya. Kembalikan scaling-nya setelah itu.
- [ ] Dengan `-v`, setelah "Cyrene, lihat layarku", log tidak memuat `Request options`.
- [ ] Dengan `vision.enabled: false`, `waifu check` menampilkan `! Melihat layar mati` dan
      "Cyrene, lihat layarku" tidak mengambil tangkapan layar.

**Artinya apa** (lihat [Artinya apa](#artinya-apa))

- [ ] Copy 「お疲れ様でした！また明日ね」, lalu "Cyrene, artinya apa yang kucopy?": dia mengucapkan
      intinya tanpa romaji dan kartu Terjemahan tampil dengan kana, romaji, dan daftar kata; log
      menampilkan `Read the clipboard (... characters)`, tanpa teksnya.
- [ ] Di game borderless, copy teks lain lalu Ctrl+Alt+T: jawabannya sama tanpa mic; selagi dia
      bicara Ctrl+Alt+T tidak melakukan apa pun, dan Ctrl+Alt+X lalu Ctrl+Alt+T langsung bertanya.
- [ ] Copy sebuah kata sandi palsu (misalnya `Kucing123!`), lalu Ctrl+Alt+T: dia bilang
      kelihatannya rahasia tanpa membacakannya, dan log serta `-v` tidak memuatnya.
- [ ] Copy sebuah gambar, lalu "artinya apa yang kucopy?": dia bilang yang dicopy bukan teks.
- [ ] Dengan belajar bahasa Jepang menyala, sesudah terjemahannya "simpan kata itu": kuis
      berikutnya menanyakan kata itu, dan `waifu jepang progres` menampilkan `Kata tambahanmu`.
- [ ] `waifu check` menampilkan `Ctrl+Alt+T artinya apa` di baris `Hotkey global`.

**Sapaan pagi** (isi dulu `briefing.city` di `config.yaml`; lihat
[Mengatur kota](#mengatur-kota))

- [ ] `waifu check` menampilkan `✓ Sapaan pagi: jam 07:00-12:00, cuaca untuk <kotamu>`.
- [ ] `waifu briefing` menampilkan kota dan provinsi yang benar, cuaca yang masuk akal
      dibanding langit di luar, dan jadwal hari ini; exit code-nya 0.
- [ ] Hapus `data\briefing.json`, lalu jalankan `waifu run` di dalam jendela jamnya (atau
      atur sementara `time` sebelum jam sekarang): dia menyapa sekali dengan cuaca dan
      jadwalnya, dan HP menerima teks yang sama kalau notifikasi menyala. Balasan tanpa
      memanggil namanya ("makasih ya") langsung dijawab.
- [ ] Jalankan ulang `waifu run` pagi itu juga: dia tidak menyapa lagi.
- [ ] Dengan otak Claude: hapus lagi `data\briefing.json`, matikan Wi-Fi, lalu jalankan
      `waifu run`: dia tidak mengucapkan apa pun dan HP tidak menerima apa-apa; log
      menampilkan `Not saying the answer to a request`. Nyalakan lagi Wi-Fi-nya.
- [ ] Kapan saja: "Cyrene, cuaca hari ini gimana?" dan "Cyrene, hari ini ada jadwal apa?"
      dijawab dengan data yang sama.

**Peringatan hujan** (isi dulu `briefing.city`; lihat
[Peringatan hujan](#peringatan-hujan))

- [ ] `waifu check` menampilkan
      `✓ Peringatan hujan: 60 menit sebelumnya, peluang 60% ke atas, jam 06:00-21:00, untuk <kotamu>`.
- [ ] `waifu rain` menampilkan kota dan provinsi yang benar, dan jam hujan berikutnya kira-kira
      sama dengan prakiraan per jam di aplikasi cuaca HP; exit code-nya 0.
- [ ] Pada hari yang diperkirakan hujan sore, biarkan `waifu run` menyala: sekitar satu jam
      sebelumnya dia mengucapkan "<panggilanmu>, sekitar jam ... kemungkinan hujan di <kotamu>,
      angkat jemuran ya." sekali, dan HP menerima kalimat yang sama kalau notifikasi menyala.
      Selama hujan dan sesudahnya dia tidak mengulang.
- [ ] Ngobrol dengannya tepat saat peringatannya datang: peringatannya baru diucapkan sesudah
      jendela lanjutannya lewat, tidak memotong jawabannya.
- [ ] Atur sementara `active_until` sebelum jam sekarang dan jalankan ulang `waifu run`: tidak
      ada peringatan (log tidak menampilkan `Rain is likely`). Kembalikan pengaturannya.
- [ ] Matikan Wi-Fi sebentar: dia tidak mengucapkan apa pun, dan log menampilkan
      `Could not read the rain forecast` sekali saja. Nyalakan lagi Wi-Fi-nya.

**Cuaca beberapa hari** (isi dulu `briefing.city`; lihat
[Cuaca beberapa hari](#cuaca-beberapa-hari))

- [ ] `waifu cuaca besok` menampilkan kota dan provinsi yang benar, dan kondisi serta peluang
      hujan tiap bagian hari kira-kira sama dengan prakiraan di aplikasi cuaca HP; exit
      code-nya 0. `waifu cuaca akhir-pekan` menampilkan Sabtu dan Minggu berikutnya (pada hari
      Sabtu: sisa hari ini dan Minggu; pada hari Minggu: sisa hari ini saja).
- [ ] "Cyrene, besok cuaca gimana?", "Cyrene, nanti sore hujan nggak?", dan "Cyrene, akhir
      pekan hujan nggak?" dijawab singkat untuk kotamu; "Cyrene, cuaca hari ini gimana?" tetap
      dijawab dari ringkasan hari (dengan `briefing.enabled: false`, dari prakiraan sisa hari
      ini).
- [ ] "Cyrene, cuaca sepuluh hari lagi gimana?": dia bilang prakiraannya hanya sampai hari
      keenam.

**Biaya API** (otak Claude; lihat [Biaya API Claude](#biaya-api-claude))

- [ ] Setelah beberapa kalimat di `waifu run`, "Cyrene, biaya API hari ini berapa?" dijawab
      dengan perkiraan yang dibulatkan, dan `waifu usage` menampilkan hari ini dengan jumlah
      permintaan yang masuk akal (satu kalimat bisa beberapa permintaan kalau ada tool).
- [ ] `waifu check` menampilkan `✓ Biaya API: $<biaya> bulan ini (<n> permintaan)`, sama dengan
      baris Total di `waifu usage`.
- [ ] Kalau hanya Cyrene yang memakai API key itu, biaya hari ini di Console (Usage) kira-kira
      sama dengan `waifu usage` keesokan harinya (Console bisa terlambat beberapa jam).
- [ ] Isi `usage.monthly_budget_usd` sedikit di bawah biaya bulan ini (misalnya `0.01`) dan
      jalankan ulang `waifu run`: setelah jawaban berikutnya, begitu jendela lanjutannya
      lewat, dia mengucapkan pemberitahuan 80% lalu batas anggaran dengan wajah kaget, dan HP
      menerimanya kalau notifikasi menyala.
      Restart dan ngobrol lagi: tidak diulang. Setelah itu kembalikan anggarannya, dan saat
      Cyrene mati hapus bulan ini dari bagian `notices` di `data\usage.json` supaya anggaran
      yang sebenarnya masih memberi tahu bulan ini.

**Cyrene di HP** (lihat [Cyrene di HP](#cyrene-di-hp))

- [ ] `waifu phone setup` menampilkan nama PC di Tailscale; setelah perintah `tailscale serve`
      dan `phone` di `config.yaml`, `waifu check` menampilkan `✓` untuk semua baris HP kecuali
      HP terpasang.
- [ ] Dengan Wi-Fi HP dimatikan (data seluler, Tailscale menyala), alamat `https://...ts.net/`
      menampilkan formulir pasangan; kode dari `waifu phone pair` memasangkannya, dan
      `waifu phone devices` menampilkannya.
- [ ] Di HP: "nyalakan lampu kamar" diketik → lampu menyala, jawabannya tampil dan terdengar
      di HP dengan mulut avatar bergerak, dan speaker PC diam.
- [ ] Tahan tombol mikrofon dan ucapkan "matikan lampu kamar" → yang terdengar tampil, lampu
      mati. "matikan komputer" dari HP ditanyakan dulu; jawab "tidak".
- [ ] Pasang di layar utama (Chrome → Tambahkan ke layar utama): ikon Cyrene membuka halaman
      tanpa bilah alamat.
- [ ] Alamat yang sama dari perangkat yang tidak login Tailscale tidak terbuka; dari browser
      lain di tailnet yang belum dipasangkan hanya muncul formulir pasangan.
- [ ] `waifu phone forget d1` → halaman di HP kembali ke formulir pasangan di permintaan
      berikutnya.
- [ ] Dengan otak Claude, `waifu check` menampilkan `✓ Kamera HP: aktif`. Di HP ketuk tombol
      kamera: Chrome meminta izin kamera; izinkan. Jendela kamera menampilkan kamera
      belakang, tombol putar mengganti ke kamera depan, dan tanda kamera Android muncul.
- [ ] Arahkan ke sebuah benda dan ketik "ini apa?" → gelembungmu menampilkan gambar kecilnya
      dan Cyrene menyebut benda itu. Tahan tombol mikrofon, tanya hal lain sambil mengarahkan
      ke benda lain: dia menjawab tentang foto yang baru.
- [ ] Tutup jendela kamera dengan **×**: tanda kamera Android hilang, dan pesan berikutnya
      tidak membawa foto (tanpa gambar kecil di gelembung).
- [ ] Dengan jendela kamera terbuka, ketik "coba lihat ini": dia menjawab tentang foto, dan log
      `waifu run` tidak memuat `Tool look_at_screen {} -> ok`. Ketik "lihat layarku": dia
      menjawab tentang layar PC.
- [ ] Buka jendela kamera lalu diamkan 2 menit: kamera mati sendiri. Buka lagi lalu pindah ke
      aplikasi lain: kamera langsung mati.
- [ ] Log `waifu run` hanya mencatat ukuran foto (`carries a photo of ... bytes, ...`), tanpa
      isinya, dan tidak ada file foto baru di PC.
- [ ] `camera: false` di bagian `phone`, jalankan ulang `waifu run`, muat ulang halaman HP:
      tombol kamera hilang, dan `waifu check` menampilkan
      `! Kamera HP mati (phone.camera: false)`.
- [ ] Di HP ketuk tombol lampu di kiri kotak pesan: panel Tombol cepat terbuka dengan lampu,
      AC, dan rutinitas, tanpa PC dan colokan PC, tiga tombol sebaris, dan bisa digulir.
- [ ] Ketuk **Nyala** di Lampu kamar: lampu menyala dan "Lampu kamar dinyalakan." muncul
      sebentar; ketuk **24°C** di AC: AC berbunyi dan berganti ke 24.
- [ ] Ketuk **Mode tidur**: semua langkahnya jalan, pesannya menyebut jam langkah yang ditunda,
      dan `waifu schedule list` menampilkannya.
- [ ] Log `waifu run` mencatat `Phone device d1 ran quick action ...: ok`, tanpa token HP.

**Kesehatan PC** (lihat [Kesehatan PC](#kesehatan-pc))

- [ ] `waifu pc` menampilkan CPU, RAM, setiap disk dengan sisa yang sama dengan Explorer, GPU
      dengan suhu dan VRAM, dan waktu sejak restart; CPU, RAM, dan GPU kira-kira sama dengan tab
      Performance di Task Manager. `waifu check` menampilkan
      `✓ Kesehatan PC: aktif, GPU NVIDIA GeForce RTX 5060 Ti`.
- [ ] Di `waifu run`: "Cyrene, PC-ku gimana?" dijawab dalam satu atau dua kalimat dengan angka
      yang dibulatkan, sesuai `waifu pc`. "Cyrene, suhu CPU-ku berapa?": dia bilang tidak bisa
      membacanya.
- [ ] Isi `pc_health.disk_warn_gb` di atas sisa disk C (misalnya `2000`) dan jalankan ulang
      `waifu run`: begitu dia siap, dia bilang disk C (dan setiap disk lain yang sisanya di bawah
      batas itu) hampir penuh, satu kalimat per disk, dengan wajah sedih, dan HP menerima
      masing-masing kalau notifikasi menyala. Ditunggu beberapa menit: tidak ada yang diulang.
      Setelah itu kembalikan nilainya.
- [ ] Isi `gpu_warn_c: 60` dan `gpu_warn_minutes: 1`, jalankan ulang `waifu run`, lalu main
      game yang berat: setelah sekitar 1-2 menit dia bilang GPU-nya panas, dan tidak diulang
      dalam satu jam. Setelah itu kembalikan nilainya.

**Jam main** (lihat [Jam main](#jam-main))

- [ ] Dengan `waifu run` menyala, buka HSR beberapa menit lalu tutup: `waifu playtime`
      menampilkan HSR dengan waktunya, dan selagi masih terbuka `sedang main`. Hanya launcher
      HoYoPlay yang terbuka: tidak ada yang tercatat. `waifu check` menampilkan
      `✓ Jam main: aktif (HSR, Genshin, ZZZ)`.
- [ ] Buka Genshin dan ZZZ masing-masing sekali: keduanya tercatat. Kalau tidak, lihat nama
      programnya di Task Manager → Details dan tulis di `play_time.games`.
- [ ] Restart `waifu run` selagi main: `waifu playtime` tetap menampilkan satu sesi. Sleep PC
      beberapa menit dengan game terbuka: waktu sleep tidak terhitung.
- [ ] "Cyrene, hari ini aku main berapa lama?": dijawab singkat, sesuai `waifu playtime`.
- [ ] Isi `play_time.bedtime` beberapa menit ke depan (antara 18:00 dan 05:59), jalankan ulang
      `waifu run` dengan game terbuka: dia mengingatkan sekali; restart `waifu run`: tidak
      diulang malam itu. Setelah itu kosongkan lagi.

**Pengingat saat game dibuka** (lihat [Pengingat saat game dibuka](#pengingat-saat-game-dibuka))

- [ ] Dengan HSR tertutup, "Cyrene, pas buka HSR nanti, ingetin aku farming relic": dia bilang
      akan mengingatkan; `waifu pengingat-game` menampilkannya. Buka HSR: dalam semenit dia
      bilang "kamu baru buka HSR, jangan lupa farming relic", sekali, lalu pengingatnya hilang
      dari `waifu pengingat-game`.
- [ ] "Tiap buka Genshin ingetin cek ekspedisi", lalu buka dan tutup Genshin dua kali (tutup
      lebih dari 2 menit): diucapkan di kedua pembukaan.
- [ ] Dengan pengingat "tiap kali" untuk HSR yang sudah diucapkan saat HSR dibuka, restart
      `waifu run` dengan HSR tetap terbuka: tidak diucapkan lagi; sleep PC beberapa menit dengan
      HSR terbuka: tidak diucapkan juga.
- [ ] Selagi HSR terbuka, "nanti kalau buka HSR lagi ingetin daily": dia bilang HSR sedang
      terbuka dan akan mengingatkan di pembukaan berikutnya.

**Teman fokus** (lihat [Teman fokus](#teman-fokus))

- [ ] `waifu check` menampilkan `✓ Teman fokus: aktif`.
- [ ] "Cyrene, temani aku fokus 5 menit": dia menjawab singkat, dan "sisa berapa menit?" dijawab
      sesuai. Setelah 5 menit dia bilang fokusnya selesai dan memuji; `waifu focus` menampilkan
      blok itu.
- [ ] "Cyrene, fokus 5 menit, istirahat 1 menit, dua kali": di akhir blok pertama dia bilang
      istirahat, dan satu menit kemudian "Yuk, lanjut fokus lagi."
- [ ] Sebelum mulai, pasang pengingat untuk beberapa menit lagi: pengingatnya tetap berbunyi di
      tengah blok. Pemberitahuan lain yang datang selama blok (misalnya pengingat tidur jam main
      dengan `play_time.bedtime` beberapa menit ke depan dan game terbuka) menunggu sampai
      istirahat dan diucapkan sesudah kalimat istirahatnya.
- [ ] Selama blok, panggil "Cyrene?": dia menjawab seperti biasa.
- [ ] "Cyrene, berhenti fokusnya": sesinya berhenti. Hentikan `waifu run` dengan Ctrl+C di
      tengah blok lalu jalankan lagi: dia tidak melanjutkan sesinya, dan `waifu focus` mencatat
      bagian blok yang sudah berjalan.

**Mode game** (lihat [Mode game](#mode-game))

- [ ] Tulis `game_mode` dengan `announcements: hold`, `on_start: redup`, dan `on_stop: terang`
      (dua rutinitas lampu seperti di bagian Mode game), lalu jalankan ulang `waifu run`:
      `waifu check` menampilkan `✓ Mode game: pengumuman ditahan selama main` dengan kedua
      rutinitasnya.
- [ ] Buka HSR: dalam semenit lampu meredup tanpa kata. Alt-tab ke aplikasi lain dan
      minimalkan game beberapa menit: lampunya tetap.
- [ ] Selama main, pasang pengingat untuk dua menit lagi: tetap berbunyi tepat waktu.
      Pemberitahuan lain yang datang selama main (misalnya kode redeem baru) belum diucapkan.
- [ ] Tutup game: sekitar 1-2 menit kemudian lampu terang lagi, dan pemberitahuan yang tertunda
      diucapkan sekaligus, dibuka dengan "Tadi selama main ada kabar.". Pengingat resin penuh
      yang datang lebih dari 15 menit sebelumnya tidak ikut diucapkan.
- [ ] Buka game lagi, restart `waifu run` selagi main: lampu tidak diubah lagi. Tutup game:
      lampu terang.

**Jurnal harian** (lihat [Jurnal harian](#jurnal-harian))

- [ ] `waifu check` menampilkan `✓ Jurnal: 0 hari tercatat, pertanyaan malam mati`.
- [ ] "Cyrene, catat di jurnal: hari ini aku mencoba jurnal": dia menjawab singkat tanpa
      membacakannya, dan `waifu jurnal show` dengan tanggal hari ini menampilkannya. "Cyrene,
      hari ini aku nulis apa di jurnal?": dirangkum singkat.
- [ ] "Cyrene, hapus jurnal hari ini": dia tidak menghapus apa pun, tapi memberi tahu
      `waifu jurnal forget`.
- [ ] Isi `journal.ask_at` beberapa menit ke depan dan jalankan ulang `waifu run`: dia bertanya
      sekali bagaimana hari Master. Jawab langsung tanpa memanggil namanya: jawabannya muncul di
      `waifu jurnal show`. Restart `waifu run`: dia tidak bertanya lagi hari itu. Setelah itu
      kosongkan lagi, atau isi dengan jam yang Master suka.
- [ ] Sapaan pagi berikutnya tidak menyebut isi jurnal.

**Rekap hari ini** (lihat [Rekap hari ini](#rekap-hari-ini))

- [ ] Selesaikan satu tugas, fokus sebentar dengan teman fokus, dan tulis satu catatan jurnal:
      `waifu rekap` menampilkan ketiganya (dan jam main kalau game sempat terbuka), exit
      code-nya 0.
- [ ] "Cyrene, hari ini aku ngapain aja?" dijawab dua-empat kalimat yang hangat, tanpa
      membacakan isi jurnal dan tanpa menyebut uang; "Cyrene, rekap kemarin dong" menceritakan
      kemarin.
- [ ] "Cyrene, rekap tanggal <hari 40 hari yang lalu>": dia bilang rekap hanya sampai 30 hari ke
      belakang.
- [ ] Dengan mode tamu menyala, "Cyrene, hari ini aku ngapain aja?" ditolak singkat.

**Pencarian web dan promo gratis** (lihat
[Pencarian web dan promo gratis](#pencarian-web-dan-promo-gratis))

- [ ] Dengan otak Claude, `waifu check` menampilkan `✓ Pencarian web: aktif` dan
      `✓ Promo gratis: game Epic, Steam, GOG; promo AI belum pernah dicari`.
- [ ] "Cyrene, harga RTX 5070 sekarang berapa?" dijawab dalam satu-dua kalimat dengan menyebut
      sumbernya, tanpa membacakan link; `waifu usage` menampilkan kolom `PENCARIAN WEB`.
- [ ] `waifu offers games` menampilkan game gratis yang sama dengan halaman Free Games di Epic
      Games Store (judul dan jam berakhirnya) dan baris `Sumber: GamerPower.com` kalau ada game
      dari Steam atau GOG; link-linknya membuka halaman gamenya.
- [ ] `waifu offers ai --scan` selesai dalam beberapa menit, menampilkan promo AI dengan link ke
      halaman penyedianya dan perkiraan biayanya; buka beberapa linknya dan cocokkan isinya.
- [ ] Hapus `data\free_offers.json` saat Cyrene mati, nyalakan notifikasi HP, lalu jalankan
      `waifu run`: begitu siap dia menyebut game gratis yang sedang ada (dan promo AI setelah
      pencarian pertamanya selesai); HP menerima notifikasinya, dan mengetuknya membuka link
      yang pertama. Jalankan ulang `waifu run`: tidak ada yang diulang.
- [ ] "Cyrene, ada promo AI gratis nggak?" lalu "kirim linknya ke HP": HP menerima notifikasi
      dengan judul promonya, dan mengetuknya membuka halaman penyedianya. Dia tidak membuka apa pun
      di PC.

**Keuangan dari tepira** (setelah langkah 1–7 di [Menyiapkan](#menyiapkan); buka dashboard tepira
di sampingnya)

- [ ] `waifu check` menampilkan baris `Keuangan tepira` yang sesuai setelan `writes` dan otaknya,
      tanpa token atau potongannya.
- [ ] `waifu tepira`, `waifu tepira dompet`, `waifu tepira tujuan`, dan `waifu tepira transaksi`
      cocok dengan dashboard, halaman Dompet, tujuan tabungan, dan daftar pengeluaran di tepira.
- [ ] Satu pertanyaan suara per cerita, masing-masing dijawab satu-dua kalimat dengan angka yang
      cocok dengan tepira dan dibulatkan: "pengeluaran bulan ini berapa?", "hari ini habis
      berapa?", "pemasukan bulan ini berapa?", "skor keuanganku gimana?", "pengeluaran bulan lalu
      berapa?", "sisa budget makan?", "saldo GoPay berapa?", "kekayaan bersihku berapa?",
      "tabungan laptop sudah berapa?", "transaksi terakhir apa?".
- [ ] Dengan `writes: true` dan otak Claude: "catat jajan kopi seribu" membuatnya bertanya dengan
      nominal, keterangan, dan dompetnya; jawab "jangan": tidak ada catatan baru di tepira.
      Ulangi, lalu jawab "iya": catatannya ada di tepira dan saldo dompetnya berkurang Rp1.000.
      "Batalkan yang tadi" dalam 15 menit: catatannya hilang dan saldonya kembali.
- [ ] Sapaan pagi (hapus `data\briefing.json`, lalu `waifu run` di dalam jendela jamnya) tidak
      menyebut uang sama sekali, begitu juga notifikasi HP-nya.
- [ ] `logs\waifu.log` setelah semua itu memuat `Tool finance_summary(…) -> ok` tanpa angka dari
      tepira dan tanpa token.

**Mode tamu** (lihat [Mode tamu](#mode-tamu))

- [ ] `waifu check` menampilkan `✓ Mode tamu: "mode tamu" dan "mode tamu selesai" lewat suara,
      chat, atau HP; berakhir sendiri setelah 6 jam atau jam 05:00`.
- [ ] "Cyrene, mode tamu": dia menjawab singkat sampai jam berapa. "Pengeluaranku bulan ini
      berapa?" dan "tugasku apa aja?": dia bilang singkat itu ditutup dulu. "Nyalakan lampu
      kamar": lampunya menyala.
- [ ] "Kamu ingat aku suka apa?": dia tidak menyebut isi ingatannya. "Cyrene, mode tamu
      selesai", lalu tanyakan lagi: dia ingat.
- [ ] Dari chat HP, "mode tamu", lalu di PC "jurnal kemarin apa?": ditolak. Di HP "mode tamu
      selesai": di PC jurnalnya bisa dibaca lagi.
- [ ] `logs\waifu.log` memuat `Guest mode on until ...`, `Refused tool 'finance_summary' before
      running it`, dan `Guest mode off`, tanpa isi jawaban keuangan.

**Ulangi** (lihat [Ulangi](#ulangi))

- [ ] "Cyrene, nyalakan lampu kamar", lalu "ulangi" dalam 8 detik: jawabannya diucapkan lagi
      seketika, dan `logs\waifu.log` memuat `Repeating what she said last` tanpa baris `Tool`
      baru.
- [ ] Sesudah pengingat atau sapaan pagi, "Cyrene, tadi bilang apa?": pengumumannya diulang.
- [ ] "Cyrene, ulangi lagunya": dia menjawab lewat otaknya seperti biasa.
- [ ] "Cyrene, matikan komputer", lalu "ulangi": pertanyaannya diulang dan PC tetap menyala.
      "Iya" sesudahnya memulai shutdown; batalkan dengan "Cyrene, batalkan".
- [ ] "Cyrene, mode tamu", lalu "mode tamu selesai" dari chat HP, lalu di PC "Cyrene, ulangi":
      dia bilang "Belum ada yang bisa kuulangi, Master."

**Diagnosa suara** (lihat [Diagnosa suara](#diagnosa-suara))

- [ ] "Matikan lampu" tanpa nama, lalu "Cyrene, tadi kamu denger apa?": dia bilang mendengar
      "matikan lampu" tanpa namanya, dan lampunya tetap menyala.
- [ ] Di halaman Suara, ganti ke mic lain lalu kembali ke **Ikuti urutan di config**: tiap kali
      nama mic di atas berganti, tanpa menjalankan ulang `waifu run`.
- [ ] Dengan `audio.input_device` berisi headset Bluetooth lalu mic lain: matikan headsetnya; dia
      bilang "Sekarang aku pakai mic ..." dan tetap mendengar lewat mic kedua. Nyalakan lagi
      headsetnya: dia bilang "Mic ... tersambung lagi, aku pakai itu lagi."
- [ ] "Cyrene, mode tamu": tombol Suara di HP hilang. "Mode tamu selesai": tombol itu kembali
      saat halaman HP dibuka lagi.

**Hotkey global** (lihat [Hotkey global](#hotkey-global))

- [ ] Selagi `waifu run` jalan, `waifu check` menampilkan `✓ Hotkey global: Ctrl+Alt+C bicara,
      Ctrl+Alt+X potong, Ctrl+Alt+R ulangi, Ctrl+Alt+Q berhenti mendengarkan, Ctrl+Alt+T artinya
      apa; aktif di 'waifu run'` tanpa baris `! Hotkey`.
- [ ] Di game layar penuh, Ctrl+Alt+C: dia bilang "はい, Master?", lalu "nyalakan lampu kamar"
      tanpa namanya dijawab.
- [ ] Selagi dia membacakan jawaban panjang, Ctrl+Alt+X memotongnya seketika; sesudahnya
      Ctrl+Alt+R mengulangnya.
- [ ] Ctrl+Alt+Q: dia bilang sampai jam berapa, avatar menampilkan "Tidak mendengarkan", dan
      "Cyrene, nyalakan lampu kamar" tidak dijawab, tapi pengingat tetap diucapkan. **Dengar
      lagi** di HP: dia mendengarkan lagi dan tulisan di avatar hilang.

**Kamus Cyrene** (lihat [Kamus Cyrene](#kamus-cyrene))

- [ ] `waifu say "Pity HSR-ku 70, cek di HoYoLAB"` terdengar "Ha Es Er" dan "Hoyolab".
- [ ] `waifu say --fallback "Main ZZZ dulu"` terdengar "Zet Zet Zet" dengan suara Windows.
- [ ] "Cyrene, cara baca Acheron itu A Ke Ron": dia bertanya "Jadi Acheron kubaca A Ke Ron?
      Bilang iya kalau benar." dan `waifu lafal list` belum menampilkannya; "iya": dia bilang
      "Oke Master. Acheron mulai sekarang kubaca A Ke Ron." (Acheron masih dengan cara lama),
      dan `waifu lafal list` menampilkan `Acheron: A Ke Ron`.
- [ ] "Cyrene, cara baca Kafka itu Kaf Ka", lalu "nyalakan lampu kamar": lampunya menyala,
      dia tidak membahas pertanyaannya, dan `waifu lafal list` tidak menampilkan Kafka.
- [ ] "Cyrene, cara baca Kafka itu Kaf Ka", tunggu 10 detik, "Cyrene" (dia menjawab
      "はい、Master?"), lalu "bener": dia bilang "Oke Master. Kafka mulai sekarang kubaca Kaf
      Ka." dan `waifu lafal list` menampilkannya.
- [ ] "Cyrene, cara baca HSR itu salah" dan "Cyrene, cara baca jam itu begini" diteruskan ke
      otak tanpa ditanyakan, dan `waifu lafal list` tidak menampilkan aturan baru.
- [ ] "Cyrene, lupakan cara baca Acheron": dia bilang "Oke Master. Cara baca Acheron sudah
      kulupakan." dengan Acheron dibaca biasa lagi, dan `waifu lafal list` tidak menampilkannya;
      "Cyrene, lupakan cara baca HSR": dia bilang itu bawaan.
- [ ] Dengan TV menyala, "Cyrene, nyalakan" dengan alias lampumu lebih sering tertulis benar di
      log `Heard (id): ...`.

**Belajar bahasa Jepang** (lihat [Belajar bahasa Jepang](#belajar-bahasa-jepang))

- [ ] Dengan `japanese.enabled: true`, `waifu check` menampilkan `✓ Belajar bahasa Jepang: 0 dari
      300 kata dipelajari, 0 dikuasai`, dan `waifu jepang` menampilkan kata hari ini tanpa membuat
      `data\japanese.json`.
- [ ] Sapaan pagi diakhiri kata hari ini: katanya terdengar dengan suara Jepang, artinya dengan
      suara Indonesia, tanpa romaji.
- [ ] Hari pertama, "Cyrene, kuis bahasa Jepang dong": dia menanyakan satu kata saja (kata hari
      ini) lalu menunggu. Jawab salah: dia menyebut artinya dengan lembut, dan belum ada kata lain
      yang perlu diulang. `waifu jepang progres` lalu menunjukkan
      `Tidak ada kata yang perlu diulang hari ini.`
- [ ] Besoknya, kuis lagi: kata kemarin ditanya lagi. Jawab artinya dengan benar: dia memuji.
- [ ] `waifu jepang progres` menghitung kata yang sudah dipelajari dan yang perlu diulang hari ini.

**Pengingat istirahat** (lihat [Pengingat istirahat](#pengingat-istirahat))

- [ ] Tulis `breaks` dengan `enabled: true` dan `every_minutes: 20`, lalu jalankan ulang
      `waifu run`: `waifu check` menampilkan `✓ Pengingat istirahat: tiap 20 menit di depan
      layar, jeda 5 menit dihitung istirahat`.
- [ ] Pakai PC terus (mengetik atau menggerakkan mouse, jeda kurang dari 5 menit) selama 20
      menit: dia bilang sekali "..., sudah dua puluh menit di depan layar. ...". 20 menit
      kemudian: "sudah empat puluh menit".
- [ ] Tinggalkan keyboard dan mouse 6 menit, lalu kembali: 20 menit sesudah kembali dia
      mengingatkan lagi, tidak lebih cepat.
- [ ] "Cyrene, temani aku fokus 25 menit": selama blok dia tidak mengingatkan istirahat.
- [ ] Setelah selesai, kembalikan `every_minutes` ke 60 (atau hapus barisnya).

**Waktu sholat** (lihat [Waktu sholat](#waktu-sholat))

- [ ] Tulis `prayer` dengan `enabled: true` (dengan `briefing.city` atau koordinat), lalu
      jalankan ulang `waifu run`: `waifu check` menampilkan `✓ Waktu sholat: untuk ..., ihtiyath 2
      menit, pengingat subuh, dzuhur, ashar, maghrib, isya`.
- [ ] `waifu sholat` menampilkan waktu hari ini; bandingkan dengan jadwal Kemenag untuk kota
      Master: bedanya paling banyak satu atau dua menit.
- [ ] "Cyrene, jadwal sholat besok": dia membacakan waktu besok tanpa menambahkan nasihat.
- [ ] Biarkan `waifu run` menyala melewati waktu Dzuhur: dia bilang sekali "..., sudah masuk
      waktu Dzuhur.". Jalankan ulang `waifu run` beberapa menit sesudahnya: tidak diulang.

**Tombol cepat di PC** (lihat [Tombol cepat di PC](#tombol-cepat-di-pc))

- [ ] `waifu tombol`: browser terbuka di halaman Tombol cepat dengan lampu, AC, dan rutinitas,
      tanpa PC dan colokan PC, dan terminal menampilkan alamatnya.
- [ ] Klik **Nyala** di Lampu kamar: lampu menyala dan "Lampu kamar dinyalakan." muncul di
      bawah; klik **Mode tidur**: semua langkahnya jalan.
- [ ] Tekan Ctrl+C di terminal, lalu klik satu tombol di halaman: muncul "Tombol cepat di PC
      sudah ditutup. ...".

## Privasi

- **Suara tidak pernah keluar dari PC.** Rekaman mic, verifikasi suara, dan
  speech-to-text diproses lokal. Dengan Cyrene di HP, rekaman dari HP yang sudah dipasangkan
  dikirim ke PC lewat Tailscale (terenkripsi), diproses di PC, dan tidak disimpan; jawabannya
  kembali ke HP itu saja. Lihat [Privasi Cyrene di HP](#privasi-cyrene-di-hp).
- **Yang dikirim ke internet hanya teks**, kecuali tangkapan layar yang Master minta dan foto
  kamera HP yang Master kirim:
  - dengan `brain.backend: claude`, kalimat yang ditujukan ke Cyrene (sudah dalam
    bentuk teks), persona, daftar perangkat, hasil perintah perangkat, dan catatan
    ingatannya (di awal setiap sesi) dikirim ke API Anthropic, juga tangkapan layar
    monitor utama setiap kali Master memintanya melihat layar (tidak pernah disimpan ke
    disk; lihat [Privasi layar](#privasi-layar)), foto kamera HP yang dikirim bersama
    pesan dari HP selama jendela kamera terbuka (tidak pernah disimpan ke disk; lihat
    [Kamera HP](#kamera-hp)), dan teks yang Master copy saat dia memintanya membaca clipboard
    (lihat [Privasi artinya apa](#privasi-artinya-apa)). Dengan Ollama, semuanya lokal;
  - dengan engine `edge` di `tts`, teks jawaban Cyrene dikirim ke layanan edge-tts
    Microsoft untuk dijadikan suara. GPT-SoVITS, suara cadangan Windows, suara anime (RVC),
    dan avatar (VRM atau Live2D, termasuk elusan dan colekannya) berjalan lokal (halaman avatar hanya
    bisa dibuka dari PC ini);
  - dengan teman main gacha, permintaan data game ke HoYoLAB dan ke server riwayat
    HoYoverse (dengan cookie dan authkey milikmu), hasilnya ke API Anthropic kalau otaknya
    Claude, dan teks pengingat ke `ntfy.sh` (siapa pun yang tahu nama topiknya bisa
    membacanya);
  - dengan sapaan pagi, nama kota atau koordinat di `briefing` ke Open-Meteo untuk cuacanya
    (tanpa akun, tanpa data lain tentang Master), dan teks sapaannya ke `ntfy.sh` kalau
    notifikasi HP menyala. Hari sapaan terakhir hanya disimpan di `data\briefing.json`.
  - dengan peringatan hujan, tempat yang sama ke Open-Meteo setiap 15 menit di jam aktif
    selama `waifu run` menyala, dan kalimat peringatannya (panggilanmu, jam, dan kota) ke
    `ntfy.sh` kalau notifikasi HP dan `rain_alert.push` menyala. Tidak ada yang disimpan ke
    disk.
  - dengan cuaca beberapa hari, tempat yang sama ke Open-Meteo setiap kali Master
    menanyakannya (atau `waifu cuaca` dijalankan). Tidak ada yang disimpan ke disk.
  - dengan kabar versi baru, kalimatnya (panggilanmu, nama Cyrene, versi dan judulnya; kalau
    paketnya berubah, juga perintah `pip install`) ke `ntfy.sh` kalau notifikasi HP dan
    `version_notice.push` menyala; tidak ada path folder, commit, atau nama paket yang
    dikirim. Versi `waifu run` yang berjalan hanya dicatat di `data\running.json`.
- **Cookie HoYoLAB setara kata sandi.** Hanya disimpan di `config.yaml` dan tidak pernah
  ditulis ke log. Riwayat pull ada di `data\gacha\` (nama filenya memuat UID game).
- **Kode redeem:** ke `hoyo-codes.seria.moe` hanya nama game; penukaran ke HoYoLAB dengan
  cookie milikmu, hanya saat Master minta (atau dengan `auto_redeem`). Kode, hadiah, dan hasil
  penukarannya ada di `data\redeem_codes.json`, hanya di PC ini; pemberitahuannya ke `ntfy.sh`
  kalau notifikasi HP menyala. Lihat [Kode redeem](#kode-redeem).
- **Laporan game mingguan:** pull, pity, endgame, kode, jam main, pendapatan bulan ini, dan
  berapa kali internet putus minggu itu ke otaknya (dengan Claude, ke API Anthropic), tidak pernah ke log biasa; kalimat
  laporannya ke `ntfy.sh` kalau notifikasi HP dan `weekly_report.push` menyala. `data\weekly_report.json` hanya menyimpan
  tanggal laporan terakhir. Lihat [Laporan game mingguan](#laporan-game-mingguan).
- **Pendapatan gacha bulanan:** dibaca dari HoYoLAB hanya saat ditanya (dan untuk laporan
  mingguan), disimpan di memori 15 menit, tidak pernah ke disk atau log biasa; dengan Claude,
  angkanya ke API Anthropic. Lihat [Pendapatan gacha bulanan](#pendapatan-gacha-bulanan).
- **Ingatan Cyrene** (hal yang dia ketahui tentang Master) ada di `data\memory.json`,
  hanya di PC ini; rahasia seperti kata sandi atau nomor kartu tidak pernah disimpan.
  Lihat [Privasi ingatan](#privasi-ingatan).
- **Jadwal** (pengingat, perintah perangkat terjadwal) ada di `data\schedule.json`, hanya di
  PC ini. Teks pengingat juga dikirim ke `ntfy.sh` kalau notifikasi HP menyala. Lihat
  [File data\schedule.json](#file-dataschedulejson).
- **Alarm bangun:** alarm ada di `data\schedule.json`; selagi berbunyi, `data\alarm.json` berisi
  id, jam, dan keterangannya, lalu dihapus. Keterangannya ke API Anthropic dengan Claude, dan bunyi
  pertama serta alarm yang terlewat ke `ntfy.sh` kalau notifikasi HP menyala. Lihat
  [Privasi alarm](#privasi-alarm).
- **Diagnosa suara:** rekaman tidak pernah disimpan; catatan ucapan (dengan teks yang ditangkap
  Whisper) hanya di memori dan hanya ke HP yang dipasangkan, tidak saat mode tamu. Mic dan setelan
  pilihan HP ada di `data\voice_override.json`. Lihat
  [Privasi diagnosa suara](#privasi-diagnosa-suara).
- **Hotkey global:** Cyrene hanya tahu bahwa salah satu kombinasi Ctrl+Alt-nya ditekan, tidak
  pernah tombol lain yang kamu ketik. `data\listen_pause.json` ada hanya selama dia tidak
  mendengarkan, dan `data\hotkeys.json` selama `waifu run` jalan. Lihat
  [Privasi hotkey](#privasi-hotkey).
- **Kartu teks:** kartu hanya ada di memori (avatar, `waifu run`, dan `waifu chat`), tidak
  pernah di disk atau log (log hanya mencatat jumlah baris dan karakternya), dan disembunyikan
  saat mode tamu berganti. Tautannya hanya dibuka saat kamu mengkliknya. Lihat
  [Kartu teks](#kartu-teks).
- **Kamus Cyrene:** cara baca kata ada di `data\tts_lexicon.json`, hanya di PC ini; nama
  karakter untuk Whisper hanya di memori selama `waifu run` jalan. Lihat
  [Privasi kamus](#privasi-kamus).
- **Daftar tugas** ada di `data\todo.json`, hanya di PC ini; rahasia seperti kata sandi atau
  nomor kartu tidak pernah disimpan. Lihat
  [File data\todo.json dan privasi tugas](#file-datatodojson-dan-privasi-tugas).
- **Lagu di YouTube:** dengan Claude, judul lagu yang diminta menjadi kata kunci pencarian web
  Anthropic; halamannya dibuka di browser default seperti biasa, jadi YouTube melihatnya bersama
  akun Google di browser itu. Lagunya tidak disimpan Cyrene. Lihat
  [Privasi lagu](#privasi-lagu).
- **Catatan biaya API** (jumlah token dan biaya per hari, tanpa isi obrolan) ada di
  `data\usage.json`, hanya di PC ini. Pemberitahuan anggaran juga dikirim ke `ntfy.sh` kalau
  notifikasi HP menyala. Lihat [File data\usage.json](#file-datausagejson).
- **Kesehatan PC** hanya berisi angka, huruf drive, dan nama model GPU (tanpa nama program,
  file, atau user) dan tidak disimpan. Dengan Claude, angkanya ikut ke API Anthropic saat Master
  bertanya; pemberitahuannya ke `ntfy.sh` kalau notifikasi HP menyala. Lihat
  [Privasi kesehatan PC](#privasi-kesehatan-pc).
- **Jam main:** dari daftar proses Windows hanya nama tiga game yang dipakai; game mana yang
  terbuka dari jam berapa sampai jam berapa ada di `data\playtime.json`, hanya di PC ini (90
  hari). Tanpa pemberitahuan HP sendiri, tapi kalimat laporan game mingguan yang menyebut jam
  main ke `ntfy.sh` kalau notifikasi HP dan `weekly_report.push` menyala; dengan Claude, totalnya
  ke API Anthropic saat Master bertanya dan di laporan game mingguan. Lihat
  [Privasi jam main](#privasi-jam-main).
- **Pemantau internet:** `data\network.json` hanya berisi kapan internet putus dan jenisnya,
  hanya di PC ini (7 hari). Ceknya ke router, 1.1.1.1, 8.8.8.8, server DNS, dan selagi game
  dimainkan server HoYoLAB/HoYoverse, tanpa mengirim data. Kalimat dan tool-nya tidak pernah
  menyebut alamat; dengan Claude, hasil `network_status` ke API Anthropic saat ditanya. Lihat
  [Privasi pemantau internet](#privasi-pemantau-internet).
- **Pengingat saat game dibuka:** `data\app_reminders.json` (game, teks, sekali atau tiap kali)
  hanya di PC ini. Pengingat yang jatuh waktunya ke `ntfy.sh` kalau notifikasi HP menyala, seperti
  pengingat jadwal; dengan Claude, teksnya ke API Anthropic saat Master membuat, melihat, atau
  menghapusnya. Lihat [Privasi pengingat game](#privasi-pengingat-game).
- **Teman fokus:** `data\focus.json` hanya berisi kapan tiap blok fokus mulai dan selesai, hanya
  di PC ini (90 hari). Dengan Claude, waktunya ke API Anthropic saat Master bertanya; kalimatnya
  ke `ntfy.sh` hanya dengan `focus.push` dan notifikasi HP menyala. Lihat
  [Privasi teman fokus](#privasi-teman-fokus).
- **Mode game:** tidak menyimpan atau mengirim apa pun yang baru; apakah game sedang terbuka
  hanya diingat selama `waifu run` berjalan, dan dengan `phone` tidak ada notifikasi HP
  tambahan. Lihat [Privasi mode game](#privasi-mode-game).
- **Jurnal harian:** `data\journal.json` hanya di PC ini, disimpan sampai Master menghapus
  harinya; rahasia seperti kata sandi atau nomor kartu tidak pernah disimpan. Dengan Claude,
  catatan yang ditulis atau dibaca ikut ke API Anthropic; tidak pernah ke `ntfy.sh`, sapaan pagi,
  atau laporan mingguan. Lihat [Privasi jurnal](#privasi-jurnal).
- **Rekap hari ini:** hanya membaca catatan yang sudah ada di PC ini (tugas, fokus, jam main,
  riwayat pull tersimpan, kata Jepang, jumlah catatan jurnal, jadwal, internet putus) dan tidak menyimpan atau
  mengirim apa pun sendiri. Dengan Claude, hasilnya ke API Anthropic saat Master bertanya, tanpa
  isi jurnal dan tanpa uang; tidak pernah ke `ntfy.sh`. Lihat [Privasi rekap](#privasi-rekap).
- **Pencarian web dan promo gratis:** dengan Claude, kata kunci pencarian (ditambah negara,
  zona waktu, dan kota) ke pencarian Anthropic; permintaan daftar game ke Epic dan
  GamerPower.com tanpa data tentang Master; pencarian promo AI mingguan ke API Anthropic
  tanpa data pribadi selain negara (Indonesia). Game yang sudah diberitahukan dan promo AI
  yang ditemukan ada di `data\free_offers.json`, hanya di PC ini; pemberitahuannya dan
  linknya ke `ntfy.sh` kalau notifikasi HP menyala. Lihat
  [Privasi pencarian dan promo](#privasi-pencarian-dan-promo).
- **Keuangan dari tepira:** tepira hanya dibaca saat Master bertanya, tidak pernah di sapaan pagi
  atau notifikasi HP. Dengan Claude, angka, nama dompet dan tujuan, dan catatan transaksi ikut ke
  API Anthropic dan tetap di riwayat sesi sampai sesinya berakhir; dengan Ollama tetap di PC.
  Tidak ada yang disimpan ke disk. Tokennya hanya di `config.yaml`, hanya dikirim di header ke
  tepira, dan tidak pernah ditulis ke log; log INFO menulis panggilan tool keuangan tanpa isinya,
  tapi kalimat Master yang terdengar tetap tercatat. Lihat [Privasi keuangan](#privasi-keuangan).
- **Mode tamu:** tidak menyimpan atau mengirim apa pun yang baru; hanya diingat selama
  `waifu run` berjalan. Pengingat yang Master pasang dan pemberitahuan lain tetap diucapkan
  selama mode tamu. Lihat [Privasi mode tamu](#privasi-mode-tamu).
- **Ulangi:** kalimat terakhirnya hanya diingat di memori selama `waifu run` berjalan dan
  dilupakan begitu mode tamu menyala atau mati. Pengulangannya tidak pernah mencatat isinya di
  log; jawabannya juga tidak, tapi pengingat dan pengumuman memang sudah tercatat sebagai
  `Announcing: ...` saat pertama diucapkan. Lihat [Ulangi](#ulangi).
- **Belajar bahasa Jepang:** `data\japanese.json` hanya berisi kode kata, kotaknya, dan tanggal,
  ditambah kata yang Master simpan sendiri, hanya di PC ini. Dengan Claude, kata dan jawaban
  Master ikut ke API Anthropic seperti obrolan biasa; tidak ada notifikasi HP sendiri. Lihat
  [Privasi belajar bahasa Jepang](#privasi-belajar-bahasa-jepang).
- **Pengingat istirahat:** tidak menyimpan apa pun; yang dibaca hanya berapa lama sejak keyboard
  atau mouse terakhir disentuh, bukan tombolnya, layarnya, atau aplikasinya, dan hitungannya hanya
  diingat selama `waifu run` berjalan. Kalimatnya ke `ntfy.sh` hanya dengan `breaks.push` dan
  notifikasi HP menyala. Lihat [Privasi pengingat istirahat](#privasi-pengingat-istirahat).
- **Waktu sholat:** dihitung di PC ini; yang ke Open-Meteo hanya nama kota, seperti untuk cuaca.
  `data\prayer.json` hanya berisi tanggal dan pengingat mana yang sudah diucapkan, bukan apakah
  Master sholat. Kalimatnya ke `ntfy.sh` hanya dengan `prayer.push` dan notifikasi HP menyala.
  Lihat [Privasi waktu sholat](#privasi-waktu-sholat).
- **Tombol cepat di PC:** halamannya hanya di `127.0.0.1` dan hanya dengan token acak di
  alamatnya, yang tidak pernah ditulis ke log; tidak ada yang disimpan atau dikirim ke luar PC.
  Lihat [Privasi tombol cepat di PC](#privasi-tombol-cepat-di-pc).
- **Voiceprint adalah data biometrik.** `data/voiceprint.npy` (dan seluruh `data/`)
  ada di `.gitignore` dan tidak boleh di-commit atau dibagikan. Begitu juga
  `config.yaml` (local key Tuya) dan `models/`.
- **Log berisi transkrip.** `logs\waifu.log` (dan `.1` sampai `.3`, total sekitar
  4 MB; ada di `.gitignore`) mencatat setiap kalimat Master yang terdengar
  (`Heard ...`), termasuk yang tidak ditujukan ke Cyrene, serta setiap perintah
  perangkat. Autostart selalu menulis log ini. Periksa isinya sebelum membagikan file
  log atau output `waifu autostart status` (yang menampilkan 5 baris terakhirnya), atau
  hapus folder `logs\` kalau tidak diperlukan.
- File hasil wizard tinytuya (`tinytuya.json`, `devices.json`, `snapshot.json`,
  `tuya-raw.json`) berisi API secret dan local key. `waifu setup tuya` menjalankan wizard di
  folder proyek, dan `.gitignore` mencakup keempat file itu; setelah impor Cyrene menawarkan
  menghapus `tinytuya.json` dan `tuya-raw.json`. Jangan bagikan `devices.json` dan
  `snapshot.json`. Cyrene hanya membaca `devices.json` dan `snapshot.json`, tidak pernah
  membaca API Key atau API Secret, dan tidak pernah menampilkan atau mencatat local key.
- Cadangan `config.yaml` dari `waifu setup` (`data\config.yaml.bak-...`) berisi local key yang
  sama dengan `config.yaml`; seluruh `data\` ada di `.gitignore`. Topik ntfy hanya ditampilkan
  sekali, saat langkah berlangganan, dan tidak pernah dicatat di log.
- Perintah Tuya, Broadlink, dan Wake-on-LAN berjalan di jaringan lokal; Cyrene tidak
  memakai cloud Tuya. Cloud Tuya hanya dipakai wizard untuk mengambil local key.

## Pengembangan

Repo ini memakai template `.claude/` dari **claude-code-starter** (berbasis
[agent-skills](https://github.com/addyosmani/agent-skills)). Sebelum mengubah kode,
baca:

- `CLAUDE.md`: aturan kerja, konvensi kode, dan batasan (apa yang tidak boleh
  di-commit, kapan harus bertanya dulu).
- `SPEC.md`: tujuan, user story, dan kontrak tiap modul.
- `tasks/plan.md` dan `tasks/todo.md`: rencana dan kriteria penerimaan tiap task.
- `.claude/skills/`, `.claude/commands/`, `.claude/agents/`: alur kerja yang dipakai
  Claude Code.

Alur kerja di Claude Code:

| Tahap | Perintah | Hasil |
|---|---|---|
| Definisi | `/spec` | `SPEC.md` |
| Rencana | `/plan` | `tasks/plan.md`, `tasks/todo.md` |
| Bangun | `/build` (atau `/build auto`) | satu task per commit, test dulu baru kode |
| Review | `/review` | review kualitas kode |
| Rilis | `/ship` | pemeriksaan akhir sebelum rilis |

Test dan lint (tanpa GPU, mic, atau jaringan):

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format src tests
node --test "tests/js/*.test.mjs" "avatar-app/test/*.test.js"   # logika halaman avatar dan maskot
```

Suite test men-set `WAIFU_DISABLE_POWER=1` (`tests/conftest.py`) supaya tidak ada test
yang bisa mematikan atau men-sleep PC sungguhan; kalau variabel ini di-set (selain `0`)
saat menjalankan `waifu`, perintah daya PC (`shutdown`, `restart`, `cancel_shutdown`,
`sleep`) tidak dijalankan.
