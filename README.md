# Cyrene: AI companion untuk rumah pintar

Cyrene adalah AI companion bergaya karakter anime yang berjalan di PC Windows kamu.
Dia mendengarkan lewat satu mic USB, **hanya menanggapi suara pemiliknya** (Master),
ngobrol dalam bahasa Indonesia dengan selipan ungkapan Jepang, dan mengendalikan
rumah: lampu dan colokan Wi-Fi Tuya; AC, proyektor, dan TV lewat IR blaster; serta
PC itu sendiri. Nama dan kepribadiannya bisa diganti di `config.yaml` dan
`config/persona.md`.

Rekaman mic, deteksi bicara, verifikasi suara, dan speech-to-text diproses lokal di
PC (Whisper dan verifikasi suara memakai GPU). Otaknya bisa model lokal lewat
**Ollama** (gratis, offline) atau **Claude** lewat API.

```text
Master : "Cyrene, nyalakan lampu kamar."
Cyrene : "はい! Lampu kamar sudah menyala, Master."
Master : "Terus nyalakan TV juga."              (tanpa memanggil nama, masih dalam jendela lanjutan)
Master : "Cyrene, matikan komputer."
Cyrene : "Yakin mau dimatikan sekarang, Master?" (minta konfirmasi dulu)
```

## Daftar isi

- [Kebutuhan](#kebutuhan)
- [Arsitektur](#arsitektur)
- [Instalasi](#instalasi)
- [Memilih otak: Ollama atau Claude](#memilih-otak-ollama-atau-claude)
- [Menyambungkan perangkat](#menyambungkan-perangkat)
- [Mendaftarkan suara Master](#mendaftarkan-suara-master)
- [Pemakaian sehari-hari](#pemakaian-sehari-hari)
- [Jalan otomatis saat login](#jalan-otomatis-saat-login)
- [Suara anime (つくよみちゃん)](#suara-anime-つくよみちゃん)
- [Avatar 3D (つくよみちゃん)](#avatar-3d-つくよみちゃん)
- [Avatar Live2D & interaksi](#avatar-live2d--interaksi)
- [Teman main gacha (HoYoverse)](#teman-main-gacha-hoyoverse)
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
- Internet untuk instalasi, unduhan model pertama kali, dan suara edge-tts.
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
   │        └─ PC: shutdown, restart, sleep, Wake-on-LAN
   │
   │  jawaban teks
   ▼
 SpeechRouter: bagian Indonesia dan Jepang dipisah, masing-masing
   │           pakai suaranya sendiri (edge-tts / GPT-SoVITS)
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

Kode ada di `src/waifu/`: `devices/`, `brain/`, `voice/`, `avatar/`, `gacha/`, lalu
`notify.py`, `gate.py`, `app.py`, dan `cli.py` yang menyatukan semuanya. Detail kontrak
tiap modul ada di `SPEC.md`.

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

`config.yaml` tidak di-commit karena berisi local key perangkat. Bagian-bagiannya:

| Bagian | Isi |
|---|---|
| `assistant` | nama, `wake_words`, `followup_seconds`, `persona_file`, kapan obrolan direset |
| `audio` | `input_device`/`output_device` (nomor atau nama dari `waifu mics`), `vad` (kepekaan deteksi bicara) |
| `speaker` | verifikasi suara: `enabled`, `voiceprint_path`, `threshold`, `device` |
| `stt` | model Whisper, `device`, `compute_type`, `languages` |
| `brain` | `backend` (`ollama` atau `claude`), `max_tool_rounds`, lalu blok `ollama` dan `claude` |
| `tts` | suara per bahasa (`id`, `ja`): edge-tts atau GPT-SoVITS; `rvc` untuk [suara anime](#suara-anime-つくよみちゃん) |
| `ir_codes_path` | file kode IR hasil belajar (default `data/ir_codes.json`) |
| `devices` | daftar perangkat (lihat [Menyambungkan perangkat](#menyambungkan-perangkat)) |

Kunci yang tidak dikenal ditolak dengan pesan yang jelas, jadi salah ketik langsung
ketahuan saat `waifu check`. Kepribadian Cyrene ada di `config/persona.md`.

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

## Menyambungkan perangkat

Setiap perangkat di `devices` punya:

- `id`: huruf kecil, angka, atau garis bawah (`lampu_kamar`). Dipakai di CLI dan
  oleh Cyrene saat memanggil tool.
- `name` dan `aliases`: cara kamu menyebutnya ("lampu", "lampu utama").
- `type`: `tuya_light`, `tuya_plug`, `ir`, `pc`, atau `mock`, lalu blok koneksinya
  (`tuya`, `ir`, atau `pc`).

Setelah mengubah perangkat, cek dengan `waifu devices` dan `waifu check`.

### Lampu dan colokan Tuya

1. Pasang semua lampu, colokan, dan IR blaster Tuya di aplikasi **Smart Life**
   atau **Tuya Smart** (Wi-Fi 2.4 GHz).
2. Buat akun developer di Tuya IoT Platform dan hubungkan akun aplikasimu, mengikuti
   panduan tinytuya:
   <https://github.com/jasonacox/tinytuya#get-the-tuya-device-local-key>.
3. Jalankan wizard tinytuya **di folder terpisah di luar repo**, karena wizard
   menulis `tinytuya.json`, `devices.json`, `snapshot.json`, dan `tuya-raw.json` ke
   folder aktif, dan file-file itu berisi API secret serta local key:

   ```powershell
   $repo = (Get-Location).Path
   New-Item -ItemType Directory -Force $HOME\tuya-wizard | Out-Null
   Set-Location $HOME\tuya-wizard
   & "$repo\.venv\Scripts\python.exe" -m tinytuya wizard
   Set-Location $repo
   ```

4. Salin data tiap perangkat dari `devices.json` (`id`, `key`, `ip`, `version`) ke
   `config.yaml`:

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

Catatan versi protokol:

- Kebanyakan lampu dan colokan memakai **3.3**. Firmware yang lebih baru bisa memakai
  **3.4** atau **3.5**. Pakai angka yang ditemukan wizard saat memindai jaringan.
- Kalau versi atau local key salah, perintah gagal dengan pesan
  "... menolak koneksi. Cek local_key dan version-nya di config.yaml."
- **Local key berubah setiap kali perangkat dipasang ulang** di aplikasi. Jalankan
  wizard lagi setelah reset atau pairing ulang.
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

**Tuya IR universal remote.** Ambil `device_id`/`local_key`-nya dengan wizard
tinytuya seperti di atas, lalu:

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

**Aturan keselamatan:** sebelum mematikan atau me-restart PC, Cyrene **bertanya
dulu** dan baru memanggil perintahnya setelah kamu menjawab "iya". Setelah itu ada
jeda 30 detik; bilang **"Cyrene, batalkan"** untuk membatalkan (dalam 8 detik pertama
setelah dia selesai bicara, "batalkan" saja sudah cukup). Kamu juga bisa membatalkan
manual dengan `shutdown /a` atau `waifu device komputer cancel_shutdown`.

> Konfirmasi itu adalah aturan di persona (`config/persona.md`), jadi itu perilaku
> model, bukan kunci di kode. Jeda 30 detik dan "batalkan" adalah pengamannya.
> Perintah CLI `waifu device komputer shutdown` langsung menjadwalkan shutdown tanpa
> bertanya.

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
> `config.yaml` dan kendalikan colokan hanya dari aplikasi.

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
waifu mics            # pilih mic, isi audio.input_device kalau bukan mic default
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
| `waifu run --dry-run` | Mode suara dengan perangkat simulasi, tanpa menyentuh perangkat asli |
| `waifu run --no-speaker-check` | Layani semua suara, bukan hanya Master |
| `waifu run --startup-delay 15 --announce` | Tunggu 15 detik dulu, lalu setelah model dimuat dia bilang "Cyrene siap, Master." (dipakai autostart) |
| `waifu autostart enable` / `disable` / `status` | Jalan otomatis saat login Windows (lihat [Jalan otomatis saat login](#jalan-otomatis-saat-login)) |
| `waifu chat` | Ngobrol lewat teks; ketik `keluar` untuk berhenti |
| `waifu chat --dry-run -v` | Uji persona dan tool call ke perangkat simulasi (`-v` menampilkan setiap tool call) |
| `waifu chat --speak` | Ngobrol lewat teks, jawabannya juga diucapkan |
| `waifu say "Halo Master, おかえりなさい!"` | Uji suara: bagian Indonesia lalu Jepang |
| `waifu say --raw "Halo Master"` | Sama, tapi tanpa suara anime (RVC), untuk membandingkan |
| `waifu devices` | Daftar perangkat dan perintah yang tersedia |
| `waifu device <id> <perintah> [angka]` | Jalankan satu perintah, misalnya `waifu device ac temp 24` |
| `waifu learn-ir <id> <perintah>` / `waifu learn-ir <id> --missing` | Pelajari tombol remote IR |
| `waifu enroll` / `waifu speaker-test` | Daftarkan suara / cek skor kecocokan |
| `waifu mics` | Daftar perangkat audio dan nomornya |
| `waifu avatar` / `waifu avatar --demo` | Tampilkan avatar saja, tanpa mendengarkan; `--demo` mengganti ekspresi setiap 3 detik, dan reaksi elusan atau colekan dicetak di terminal (lihat [Avatar 3D](#avatar-3d-つくよみちゃん) dan [Avatar Live2D & interaksi](#avatar-live2d--interaksi)) |
| `waifu check` | Periksa config, kode IR, voiceprint, otak, paket suara, CUDA, mic, suara anime, dan avatar |

Opsi untuk semua perintah: `--config <file>` (default `config.yaml`), `--dry-run`
(perangkat simulasi), `-v` (log lebih rinci), `--log-file <file>` (log juga ditulis
ke file UTF-8; diganti file baru setiap 1 MB, 3 file lama disimpan sebagai `.1`–`.3`).

Hanya satu `waifu run` yang bisa berjalan sekaligus (kunci di `data\run.lock`). Yang
kedua langsung berhenti dengan pesan "Cyrene sudah berjalan (mungkin dari autostart)".

Contoh kalimat saat `waifu run`:

- "Cyrene, nyalakan lampu kamar." / "Cyrene, redupkan lampu jadi tiga puluh persen."
- "Cyrene, AC dua puluh empat derajat dan matikan proyektor."
- "Matikan TV, Cyrene." (nama boleh di mana saja dalam kalimat)
- "Cyrene?" saja: dia menjawab "はい、Master?" dan menunggu perintah berikutnya.
- Setelah dia menjawab, selama 8 detik (`assistant.followup_seconds`) kamu bisa
  lanjut tanpa memanggil namanya: "terus nyalakan TV juga".

Obrolan dilupakan setelah 10 menit tanpa percakapan atau 40 giliran
(`assistant.session_idle_minutes`, `assistant.max_session_turns`).

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
  framing: full           # full: seluruh badan | upper: kepala sampai pinggang
```

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
| **Klik dua kali** padanya (dua klik dalam setengah detik) | Dia langsung mendengarkan: jendela lanjutan terbuka dan dia menjawab dengan kalimat `listen` (misalnya "はい, Master?"), jadi kamu bisa bicara tanpa memanggil namanya (hanya di `waifu run`). Selagi dia sibuk (menjawab, bicara, atau mendengar Master), klik dua kali diabaikan: ulangi setelah dia selesai, atau panggil namanya |
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
| `double_click_listen` | `true` | Klik dua kali membuatnya mendengarkan |
| `idle` | `true` | Tampak bosan setelah 3 menit tanpa aktivitas |
| `welcome_back_after_min` | `10` | Sapaan saat Master kembali setelah N menit mouse diam (hanya maskot); `0` = tidak pernah |
| `reaction_cooldown_s` | `3` | Jeda minimal (detik) antar-reaksi bersuara |
| `lines` | lihat di bawah | Kalimat tiap reaksi |

```yaml
avatar:
  interactions:
    lines:
      head_pat: ["えへへ, geli, Master!", "Hehe, makasih elusannya, Master.", "Mm, enak dielus begini."]
      body_touch: ["Hei! Kaget aku, Master.", "Eh? Ada apa, Master?", "Jangan iseng, ya!"]
      welcome_back: ["おかえりなさい, Master!", "Master sudah kembali! Aku kangen."]
      listen: ["はい, Master?", "Iya, aku dengar."]
```

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
  moc3 Cubism 5 juga butuh Core dari SDK versi 5.
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
waifu notify-test                 # notifikasi tes ke HP
```

Perintah `waifu gacha` tetap jalan walau `gacha.enabled: false`; yang dinyalakan
`gacha.enabled` adalah tool game untuk otaknya dan pengingat saat `waifu run`.

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

### Pengingat

| Pengingat | Kapan | Berapa kali | Mengatur |
|---|---|---|---|
| Stamina | penuh dalam `stamina_minutes` menit, atau sudah penuh | sekali, sampai staminanya dipakai lagi atau Cyrene dijalankan ulang | `stamina_minutes` (0 = saat sudah penuh) |
| Daily | mulai `daily_time` kalau daily belum selesai | sekali per hari game (reset 03.00 WIB) | `daily_time` |
| Mingguan | hari Minggu mulai `daily_time` kalau masih ada sisa | sekali per minggu | `weekly: false` |
| Ekspedisi | semua ekspedisi HSR / Genshin selesai | sekali per gelombang (atau setelah Cyrene dijalankan ulang) | `expeditions: false` |
| Check-in | diambil sendiri; dia bilang hadiahnya | sekali per hari (berganti jam 23.00 WIB) | `checkin: false` |
| Masalah | cookie tidak berlaku, captcha, Real-Time Notes mati | sekali per hari | — |

HoYoLAB dicek tiap `poll_minutes` (bawaan 15). Pengingat daily, mingguan, masalah, dan
check-in disimpan di `data\gacha\reminders.json`, jadi Cyrene yang di-restart tidak
mengulangnya. Stamina dan ekspedisi yang masih penuh disampaikan sekali lagi setelah
restart, karena selama PC mati dia tidak tahu apakah sempat kamu pakai. Tidak ada jam
tenang: dia juga bicara di malam hari selama `waifu run` jalan.

### Ketentuan dan risiko

- Cyrene hanya **membaca** data dan mengambil check-in harian yang memang disediakan
  HoYoLAB. Dia tidak pernah memainkan game: macro atau auto-farm melanggar ketentuan
  HoYoverse dan bisa membuat akun diblokir.
- `genshin.py` adalah pustaka HoYoLAB **tidak resmi**. Check-in otomatis lewat API
  seperti ini dipakai banyak bot, tapi tidak dijamin 100% aman. Kalau ragu, pasang
  `checkin: false`.
- Kalau HoYoverse mengubah API-nya, Cyrene akan bilang data tidak bisa dibaca;
  `pip install -U genshin` di venv biasanya memperbaikinya.

### Pemecahan masalah gacha

- **"Cookie HoYoLAB tidak berlaku lagi".** Ambil ulang cookie (langkah 1).
- **"Real-Time Notes ... belum aktif".** Langkah 2.
- **"HoYoLAB minta verifikasi captcha".** Buka HoYoLAB di browser sekali; untuk
  check-in, ambil manual dulu di hari itu.
- **"Buka riwayat warp di HSR sekali, lalu tanya lagi".** Buka halaman riwayat di game
  (banner mana saja), lalu tanya lagi. Agar tidak perlu, tambahkan `cookie_token_v2`.
- **"Riwayat ... sedang dikunci game".** Tutup game sebentar, lalu coba lagi.
- **"Server riwayat ... minta pelan-pelan".** Tunggu semenit.
- **Pity berbeda dengan di game.** Riwayatnya mungkin baru terbaca sebagian: jalankan
  `waifu gacha history <game> --full`.

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

- `waifu mics` menampilkan semua perangkat (tanda `>` = mic default). Isi
  `audio.input_device` dengan nomor atau namanya.
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
begitu.

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

**Tidak ada suara.** edge-tts butuh internet. Uji dengan
`waifu say "Halo Master"`; pilih speaker lewat `audio.output_device`.

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
- [ ] `waifu speaker-test`: suaramu `✓`, suara orang lain `✗`.

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
- [ ] Setelah `waifu run` bilang siap, menutup jendelanya juga mematikan server RVC:
      `netstat -ano | findstr :9881` tidak menampilkan apa-apa lagi.

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
- [ ] Ulangi tiga langkah terakhir dengan `engine: vrm`: kontrolnya sama.

**Teman main gacha** (setelah langkah 1–4 di bagian itu)

- [ ] `waifu gacha status` menampilkan ketiga game tanpa `✗`.
- [ ] `waifu gacha history hsr --full` (lalu `genshin`, `zzz`): pity tiap banner sama
      dengan hitunganmu di game.
- [ ] `waifu notify-test` masuk ke HP.
- [ ] Di `waifu run`: "Cyrene, Trailblaze Power-ku berapa?" dijawab dengan angka yang
      sama dengan di game.
- [ ] Turunkan sementara `stamina_minutes` sampai di atas sisa waktu penuh: dalam 15
      menit dia mengingatkan lewat suara dan HP, sekali saja.

## Privasi

- **Suara tidak pernah keluar dari PC.** Rekaman mic, verifikasi suara, dan
  speech-to-text diproses lokal.
- **Yang dikirim ke internet hanya teks:**
  - dengan `brain.backend: claude`, kalimat yang ditujukan ke Cyrene (sudah dalam
    bentuk teks), persona, daftar perangkat, dan hasil perintah perangkat dikirim
    ke API Anthropic. Dengan Ollama, semuanya lokal;
  - dengan engine `edge` di `tts`, teks jawaban Cyrene dikirim ke layanan edge-tts
    Microsoft untuk dijadikan suara. GPT-SoVITS, suara anime (RVC), dan avatar (VRM
    atau Live2D, termasuk elusan dan colekannya) berjalan lokal (halaman avatar hanya
    bisa dibuka dari PC ini);
  - dengan teman main gacha, permintaan data game ke HoYoLAB dan ke server riwayat
    HoYoverse (dengan cookie dan authkey milikmu), hasilnya ke API Anthropic kalau otaknya
    Claude, dan teks pengingat ke `ntfy.sh` (siapa pun yang tahu nama topiknya bisa
    membacanya).
- **Cookie HoYoLAB setara kata sandi.** Hanya disimpan di `config.yaml` dan tidak pernah
  ditulis ke log. Riwayat pull ada di `data\gacha\` (nama filenya memuat UID game).
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
  `tuya-raw.json`) berisi API secret dan local key tapi **tidak** tercakup
  `.gitignore`; simpan di folder di luar repo seperti langkah wizard di atas.
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
