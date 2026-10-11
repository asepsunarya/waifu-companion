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
 Speaker  (mic dijeda selama dia bicara, jadi dia tidak mendengar dirinya sendiri)
```

Kode ada di `src/waifu/`: `devices/`, `brain/`, `voice/`, lalu `gate.py`, `app.py`,
dan `cli.py` yang menyatukan semuanya. Detail kontrak tiap modul ada di `SPEC.md`.

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
  - model Whisper ke `models\whisper`, saat ucapan pertama di `waifu run`. Jawaban
    pertama setelah instalasi akan lama karena unduhan ini.

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
| `tts` | suara per bahasa (`id`, `ja`): edge-tts atau GPT-SoVITS |
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
| `waifu run` | Mode suara: dengarkan mic dan jawab dengan suara (Ctrl+C untuk berhenti) |
| `waifu run --dry-run` | Mode suara dengan perangkat simulasi, tanpa menyentuh perangkat asli |
| `waifu run --no-speaker-check` | Layani semua suara, bukan hanya Master |
| `waifu chat` | Ngobrol lewat teks; ketik `keluar` untuk berhenti |
| `waifu chat --dry-run -v` | Uji persona dan tool call ke perangkat simulasi (`-v` menampilkan setiap tool call) |
| `waifu chat --speak` | Ngobrol lewat teks, jawabannya juga diucapkan |
| `waifu say "Halo Master, おかえりなさい!"` | Uji suara: bagian Indonesia lalu Jepang |
| `waifu devices` | Daftar perangkat dan perintah yang tersedia |
| `waifu device <id> <perintah> [angka]` | Jalankan satu perintah, misalnya `waifu device ac temp 24` |
| `waifu learn-ir <id> <perintah>` / `waifu learn-ir <id> --missing` | Pelajari tombol remote IR |
| `waifu enroll` / `waifu speaker-test` | Daftarkan suara / cek skor kecocokan |
| `waifu mics` | Daftar perangkat audio dan nomornya |
| `waifu check` | Periksa config, kode IR, voiceprint, otak, paket suara, CUDA, dan mic |

Opsi untuk semua perintah: `--config <file>` (default `config.yaml`), `--dry-run`
(perangkat simulasi), `-v` (log lebih rinci).

Contoh kalimat saat `waifu run`:

- "Cyrene, nyalakan lampu kamar." / "Cyrene, redupkan lampu jadi tiga puluh persen."
- "Cyrene, AC dua puluh empat derajat dan matikan proyektor."
- "Matikan TV, Cyrene." (nama boleh di mana saja dalam kalimat)
- "Cyrene?" saja: dia menjawab "はい、Master?" dan menunggu perintah berikutnya.
- Setelah dia menjawab, selama 8 detik (`assistant.followup_seconds`) kamu bisa
  lanjut tanpa memanggil namanya: "terus nyalakan TV juga".

Obrolan dilupakan setelah 10 menit tanpa percakapan atau 40 giliran
(`assistant.session_idle_minutes`, `assistant.max_session_turns`).

## Pemecahan masalah

Log `waifu run` tampil di terminal dalam bahasa Inggris. Baris seperti
`Heard (id): ...`, `Not addressed to Cyrene (no_wake_word); ignoring`, dan
`Ignoring another speaker (similarity 0.21)` menjelaskan kenapa dia diam. Tambahkan
`-v` untuk detail lebih banyak.

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

**User story** (jalankan `waifu run`; ucapan pertama setelah start tidak dihitung
karena model sedang dimuat)

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

## Privasi

- **Suara tidak pernah keluar dari PC.** Rekaman mic, verifikasi suara, dan
  speech-to-text diproses lokal.
- **Yang dikirim ke internet hanya teks:**
  - dengan `brain.backend: claude`, kalimat yang ditujukan ke Cyrene (sudah dalam
    bentuk teks), persona, daftar perangkat, dan hasil perintah perangkat dikirim
    ke API Anthropic. Dengan Ollama, semuanya lokal;
  - dengan engine `edge` di `tts`, teks jawaban Cyrene dikirim ke layanan edge-tts
    Microsoft untuk dijadikan suara. GPT-SoVITS berjalan lokal.
- **Voiceprint adalah data biometrik.** `data/voiceprint.npy` (dan seluruh `data/`)
  ada di `.gitignore` dan tidak boleh di-commit atau dibagikan. Begitu juga
  `config.yaml` (local key Tuya) dan `models/`.
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
```

Suite test men-set `WAIFU_DISABLE_POWER=1` (`tests/conftest.py`) supaya tidak ada test
yang bisa mematikan atau men-sleep PC sungguhan; kalau variabel ini di-set (selain `0`)
saat menjalankan `waifu`, perintah daya PC (`shutdown`, `restart`, `cancel_shutdown`,
`sleep`) tidak dijalankan.
