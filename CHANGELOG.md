# Yang baru di Cyrene

Ringkasan per versi, yang terbaru di atas. Rinciannya ada di README (bagian yang disebut di
setiap versi) dan di SPEC.md. Setelah memperbarui, **mulai ulang `waifu run`** supaya semuanya
aktif, lalu jalankan `waifu check`.

## v1.24: keuangan dari tepira

- Pertanyaan soal uangmu dijawab dari tepira, aplikasi keuanganmu sendiri, hanya saat ditanya:
  "pengeluaran bulan ini berapa?", "hari ini habis berapa?", "pemasukan bulan ini berapa?",
  "sisa budget makan?", "saldo GoPay berapa?", "kekayaan bersihku berapa?", "tabungan laptop
  sudah berapa?", "transaksi terakhir apa?", "skor keuanganku gimana?".
- Mencatat pengeluaran lewat suara (pilihan, `tepira.writes: true`, hanya dengan otak Claude):
  "catat jajan kopi 18 ribu" → dia bertanya dulu dengan nominal, keterangan, dan dompetnya, dan
  baru mencatat setelah kamu menjawab "iya". "Batalkan yang tadi" menghapusnya lagi dalam 15
  menit.
- Tidak pernah di sapaan pagi, pemberitahuan, atau notifikasi HP; angkanya tidak disimpan ke
  ingatan kecuali kamu minta, dan tidak ada yang ditulis ke disk.
- `waifu tepira` (ringkasan), `waifu tepira dompet`, `waifu tepira tujuan [nama]`,
  `waifu tepira transaksi [-n N]`, dan `waifu tepira token` untuk membuat token API Cyrene.
- **Masih mati** (`tepira.enabled: false`): API-nya ada di PR #21 tepira, yang belum di-merge
  dan di-deploy. Setelah itu ikuti langkah "Menyiapkan" di README (token, env Vercel, bagian
  `tepira` di `config.yaml`, `waifu check`, `waifu tepira`).
- Log INFO kini menulis panggilan tool keuangan tanpa isinya (`Tool record_expense(…) -> ok`).
- Kedua persona bawaan mendapat bagian "Keuangan Master". Kalau kamu memakai persona buatan
  sendiri, salin bagian itu dari `config/persona.md`.
- README: "Keuangan dari tepira".

## v1.23: data akun Genshin dan ZZZ

- Akun Genshin Impact dan Zenless Zone Zero dari Battle Chronicle HoYoLAB, hanya saat
  ditanya: "Furina-ku C berapa, pakai weapon apa?", "Ellen pakai W-Engine apa?", "Abyss-ku
  lantai 12 berapa bintang?", "Shiyu Defense-ku rating apa, berakhir kapan?", "banner Genshin
  apa sekarang?".
- Genshin: karakter (Constellation, weapon dan Refinement; stat, artifact, dan set untuk yang
  ditanya namanya), Spiral Abyss, Imaginarium Theater, Stygian Onslaught, serta banner dan
  event. ZZZ: agent (Mindscape Cinema; W-Engine, stat, Drive Disc, dan set untuk yang ditanya
  namanya), Shiyu Defense, Deadly Assault, serta banner dan event.
- Tool `hsr_characters`, `hsr_endgame`, dan `hsr_events` kini bernama `game_characters`,
  `game_endgame`, dan `game_events`, dengan pilihan `game`; jawaban untuk HSR tetap sama.
  Kedua persona bawaan sudah memakai nama baru. **Kalau kamu memakai persona buatan sendiri
  yang masih menyebut `hsr_*`, ganti dengan nama baru** (salin baris game_characters dari
  `config/persona.md`): nama lama membuat Cyrene salah memanggil tool dulu.
- Dengan lebih dari satu game di `gacha.games`, sebut game-nya saat bertanya banner atau
  event, juga untuk HSR: "banner HSR apa sekarang?" (tanpa nama game, Cyrene harus bertanya
  dulu game yang mana).
- `waifu gacha genshin characters|endgame|events` dan `waifu gacha zzz characters|endgame|events`.
- Battle Chronicle tiap game harus boleh dibaca (setelannya di HoYoLAB; Cyrene tidak pernah
  mengubahnya). Bacaan pertama dalam seminggu bisa membuat genshin.py mengunduh daftar nama
  karakter Genshin, tanpa data Master.
- README: "Data akun Genshin dan ZZZ".

## v1.22.1: suara kembali

- Cyrene sempat diam: Microsoft mengganti kunci layanan suaranya (Oktober 2026), sehingga
  edge-tts versi lama ditolak (403) untuk setiap suara.
- Perbaikannya edge-tts 7.3.1: `.venv\Scripts\python -m pip install -U edge-tts`, lalu mulai
  ulang `waifu run`.
- Kalau Microsoft mengganti kuncinya lagi, log `waifu run` kini menulis "Microsoft menolak
  suara ... (403)" beserta perintah perbaikannya, bukan alamat layanan yang panjang.
- README: "Tidak ada suara".

## v1.22: persona ala Cyrene di Honkai: Star Rail (pilihan)

- `config/persona_hsr.md`: sifatmu sendiri tetap ada (tenang, puitis, sedikit manja, suka
  menggoda), ditambah sifat Cyrene di game: berbelas kasih, ceria dan penuh harapan,
  menghargai hal-hal kecil dan kenangan, bicara anggun seperti bersenandung, tanpa
  membeberkan cerita Amphoreus kecuali kamu yang membahasnya. Aturan lainnya sama persis.
- Untuk mencoba: `assistant.persona_file: config/persona_hsr.md` di `config.yaml`, lalu mulai
  ulang `waifu run`. Persona semula tidak diubah.

## v1.21.1: perbaikan kecil

- Jawaban dari pencarian web tidak lagi berspasi ganda atau berspasi sebelum koma.
- "Biaya API bulan ini berapa?" ikut menyebut jumlah dan biaya pencarian web.
- Peringatan dari pustaka genshin.py tidak lagi memenuhi jendela `waifu run`.
- Catatan dan tugas yang hanya *menyebut* rahasia boleh disimpan ("ganti PIN ATM",
  "ingatkan ganti password wifi"); yang menyertakan *isinya* tetap ditolak ("PIN ATM 1234",
  "password wifi: hijau123").
- Nama game dari GamerPower tanpa embel-embel "(Steam) Key".

## v1.21: lagu di YouTube

- "Putar lagu Hope Is the Thing With Feathers", "puterin playlist lofi", "cari lagu lofi terus
  putar": dengan Claude dia mencari link lagunya lewat pencarian web lalu membukanya di
  browser; dengan Ollama halaman pencarian YouTube yang terbuka.
- Jeda, lewati, dan volume tetap lewat tombol media ("pause musik", "lagu berikutnya").
- Hanya alamat YouTube yang dibuka, dan hanya kalau kalimatmu meminta lagu.
- `waifu youtube <judul atau link>`; `music.service: youtube_music` untuk YouTube Music.
- README: "Lagu di YouTube".

## v1.20: daftar tugas

- "Tambah tugas beli susu", "catat kerjakan laporan, tenggatnya Jumat", "tugasku apa aja?",
  "laporan sudah selesai", "ganti tenggat laporan jadi Senin".
- Tugas yang jatuh tempo hari ini atau terlambat ikut disebut di sapaan pagi.
- `waifu todo`, `waifu todo add ... --due 2026-10-23`, `waifu todo done t1`.
- Perbaikan penting: permintaan ke Claude kini tetap di bawah batas 20 tool "strict" dari
  Anthropic (versi sebelumnya bisa membuat setiap jawaban gagal setelah dimulai ulang).
- README: "Daftar tugas".

## v1.19: kode redeem

- Kode redeem HSR, Genshin, dan ZZZ yang baru diberitahukan (juga ke HP): "ada 2 kode HSR
  baru... mau kutukarkan?". Jawab "iya, tukarkan" dan dia menukarkannya lewat HoYoLAB;
  hadiahnya masuk ke surat di dalam game.
- `redeem_codes.auto_redeem: true` supaya ditukarkan otomatis.
- `waifu gacha codes hsr`, `waifu gacha codes hsr --redeem`.
- README: "Kode redeem".

## v1.18: pencarian web dan penawaran gratis

- Pencarian web di obrolan: "cari berita terbaru soal ...", "harga RTX 5070 sekarang?".
  Biayanya $10 per 1.000 pencarian ditambah token, tercatat di `waifu usage`.
- Game gratis dari Epic dan GamerPower.com: "ada game gratis apa?"; game baru diberitahukan.
- Promo AI (kredit gratis, program startup atau pelajar seperti Claude for Startups), dicari
  seminggu sekali; "ada promo AI gratis?", "kirim linknya ke HP".
- `waifu offers`, `waifu offers ai --scan`.
- README: "Pencarian web dan promo gratis".

## v1.17: kesehatan PC

- "PC-ku gimana?", "GPU-ku panas nggak?", "disk C sisa berapa?".
- Dia memberi tahu sendiri kalau disk hampir penuh atau GPU terlalu panas beberapa menit.
- `waifu pc`. README: "Kesehatan PC".

## v1.16: data HSR

- "Acheron-ku Eidolon berapa?", "Memory of Chaos-ku berapa bintang?", "banner apa
  sekarang?": dari Battle Chronicle HoYoLAB, hanya dibaca.
- `waifu gacha hsr characters|endgame|events`. README: "Data akun HSR".

## v1.15.1: maskot saat main game

- Maskot tidak lagi hilang selamanya saat game layar penuh; dia kembali sendiri dan
  dipulihkan kalau Electron atau GPU-nya bermasalah.
- Kalau masih hilang, kirim baris `Cyrene mascot: ...` dari jendela `waifu run`.
- README: "Cyrene hilang saat main game".

## v1.15: kamera HP

- Tombol kamera di halaman HP: setiap pesan saat kamera terbuka membawa satu foto ("ini
  apa?"). README: bagian kamera di "Cyrene di HP".

## v1.14: Cyrene di HP

- Halaman Cyrene di HP Android lewat Tailscale: avatar, obrolan, dan tahan-untuk-bicara.
- Perlu: Tailscale di PC dan HP, `waifu phone setup`, `waifu phone pair`.
- README: "Cyrene di HP".

## v1.13: biaya API

- "Biaya API bulan ini berapa?", `waifu usage`, dan peringatan anggaran
  (`usage.monthly_budget_usd`).

## v1.12: sapaan pagi

- Sapaan pagi sekali sehari: cuaca, jadwal, game, dan hal penting hari itu.
- `assistant.owner_call` menentukan panggilannya untukmu (sekarang "Sayang").
