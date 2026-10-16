# Yang baru di Cyrene

Ringkasan per versi, yang terbaru di atas. Rinciannya ada di README (bagian yang disebut di
setiap versi) dan di SPEC.md. Setelah memperbarui, **mulai ulang `waifu run`** supaya semuanya
aktif, lalu jalankan `waifu check`.

## v1.27: laporan game mingguan

- Seminggu sekali, bawaannya Minggu jam 19.00, Cyrene bercerita sendiri tentang minggu Master
  di HSR, Genshin, dan ZZZ dalam 3-4 kalimat seperti teman yang ikut main: pull 7 hari
  terakhir dan 50/50-nya, pity yang sudah dekat, progres endgame musim ini, dan endgame,
  banner, serta event yang berakhir dalam 7 hari. Kalimat yang sama dikirim ke HP kalau
  notifikasi menyala (`weekly_report.push: true`). Hari dan jamnya bisa diatur di bagian
  `weekly_report` baru; laporannya hanya ada kalau teman main gacha menyala.
- Hanya kabar: dia tidak pernah menyarankan pull, belanja, atau top up, dan tool keuangan
  menolak di giliran itu, jadi tidak ada angka uang yang terucap atau terkirim.
- Sekali seminggu: restart di malam yang sama tidak mengulangnya, `waifu run` yang baru menyala
  sesudah jamnya tetap melapor hari itu, dan hari yang terlewat seluruhnya tidak disusulkan.
  Minggunya dihitung dari satu jam sebelum jam laporan minggu lalu, jadi pull yang baru dicatat
  HoYoverse sesudah laporan (sekitar satu jam kemudian) masuk laporan berikutnya, seberapa pun
  telatnya laporan diucapkan. Riwayat pull diperbarui dulu untuk game yang sudah punya riwayat
  tersimpan, dan angkanya dari akun yang sedang dipakai di game; bagian yang tidak terbaca
  (HoYoLAB, cookie, riwayat yang belum diimpor) disebut singkat dan sisanya tetap diceritakan,
  dengan semua bacaan dibatasi 30 detik. Bacaan yang terlambat tidak menahan `waifu gacha
  report` atau penutupan `waifu run`.
- "Cyrene, gimana minggu ini di game-ku?" memakai data yang sama, juga di `waifu chat`.
- `waifu gacha report` baru menampilkan laporannya tanpa otak, juga saat `gacha.enabled:
  false` (kode redeem-nya lalu disebut sebagai daftar tersimpan, karena hanya `waifu gacha
  codes` yang memperbaruinya); `waifu check` menampilkan hari dan jamnya.
- README: "Laporan game mingguan".

## v1.26: versi baru

- `waifu run` yang dibiarkan menyala berhari-hari kini memberi tahu sendiri kalau ada versi baru
  di folder proyek: "Sayang, ada versi baru Cyrene: v1.27, kamera HP, dan satu versi lain. Mulai
  ulang aku kalau sempat ya." Kalimat yang sama dikirim ke HP kalau notifikasi menyala
  (`version_notice.push: true`).
- Dia membandingkan `CHANGELOG.md` dan `pyproject.toml` setiap 30 menit, hanya jam 08.00-22.00
  (versi yang masuk malam hari diberitahukan begitu jam aktif mulai), dan memberi tahu sekali
  untuk setiap versi, tanpa memotong obrolan; judul versi yang diganti tidak diberitahukan
  lagi. Perbaikan kecil, juga paket yang berubah, tanpa versi baru di CHANGELOG tidak
  diberitahukan sendiri: versinya mungkin masih dikerjakan. Semuanya bisa diatur di bagian
  `version_notice` baru.
- Kalau paket Python-nya berubah, kabar versi barunya minta `pip install` dulu, dan notifikasi
  HP-nya berisi perintahnya: tutup Cyrene, jalankan `.venv\Scripts\python -m pip install -e .`
  di folder proyek, lalu jalankan lagi `waifu run`. Dia baru memberi tahu sesudah kedua file
  terbaca utuh, supaya langkah itu tidak tertinggal, dan `waifu check` serta `waifu version`
  tetap menampilkan perintahnya sesudah dia ditutup.
- `waifu version` baru menampilkan versi di disk dan versi `waifu run` yang sedang berjalan,
  dari folder mana pun; `waifu check` menampilkannya tepat di bawah "Konfigurasi valid"
  (`✓ waifu run: v1.26, terbaru` atau `! waifu run yang sedang jalan masih v1.25 ...; mulai
  ulang supaya v1.26 aktif`). Versinya dicatat di `data\running.json` setiap `waifu run`
  dimulai.
- Dia tidak pernah memulai ulang dirinya sendiri: jendela konsol baru bisa muncul di atas game
  layar penuh dan berebut kunci dengan yang lama.
- **Sekali ini mulai ulang manual:** `waifu run` dari versi lama belum mencatat versinya. Tutup
  jendela "Cyrene", lalu jalankan lagi `waifu run` (atau logout lalu login kalau pakai
  autostart); sesudah itu dia memberi tahu sendiri.
- README: "Versi baru".

## v1.25: peringatan hujan

- Selama `waifu run` menyala, Cyrene memperingatkan sendiri sebentar sebelum hujan: "Sayang,
  sekitar jam tiga sore kemungkinan hujan di Cianjur, angkat jemuran ya." Kalimat yang sama
  dikirim ke HP kalau notifikasi menyala (`rain_alert.push: true`).
- Prakiraannya dari Open-Meteo, sumber cuaca sapaan pagi, untuk kota di `briefing.city`
  (tanpa kota dia diam), dibaca setiap 15 menit. Bawaannya satu jam sebelum hujan mulai, dari
  peluang hujan 60% per jam (atau curah 0,5 mm, atau kode hujan dan badai petir), hanya jam
  06.00-21.00; semuanya bisa diatur di bagian `rain_alert` baru. Jam yang dia sebut adalah jam
  hujannya mulai: Open-Meteo mencatat hujan untuk satu jam sebelum jam yang tertulis, jadi
  angka jam 16.00 berarti "sekitar jam tiga sore", diucapkan sekitar jam 14.00.
- Satu hujan hanya diperingatkan sekali: tidak diulang saat makin dekat, saat peluangnya
  naik-turun, saat hujan turun, atau saat berhenti kurang dari tiga jam. Hujan sesudah tiga jam
  kering di prakiraan terbaru diperingatkan lagi, juga kalau prakiraan sebelumnya menyebut
  hujan panjang. Peluang hujan saja di jam yang sedang berjalan belum dianggap hujan yang
  turun, jadi hujan sesudahnya tetap diperingatkan. Malam hari dia diam, dan obrolan yang
  sedang berjalan tidak pernah dipotong. Internet mati tidak pernah diucapkan, hanya dicatat
  sekali di log.
- `waifu rain` menampilkan kapan hujan berikutnya mulai dan selesai dalam 24 jam;
  `waifu check` menampilkan pengaturannya.
- Permintaan cuaca ke Open-Meteo kini juga meminta peluang dan curah hujan per jam; sapaan
  pagi tidak berubah.
- README: "Peringatan hujan".

## v1.24: keuangan dari tepira

- Pertanyaan soal uangmu dijawab dari tepira, aplikasi keuanganmu sendiri, hanya saat ditanya:
  "pengeluaran bulan ini berapa?", "hari ini habis berapa?", "pemasukan bulan ini berapa?",
  "sisa budget makan?", "saldo GoPay berapa?", "kekayaan bersihku berapa?", "tabungan laptop
  sudah berapa?", "transaksi terakhir apa?", "skor keuanganku gimana?".
- Mencatat pengeluaran lewat suara (pilihan, `tepira.writes: true`, hanya dengan otak Claude):
  "catat jajan kopi 18 ribu" → dia bertanya dulu dengan nominal, keterangan, dan dompetnya, dan
  baru mencatat setelah kamu menjawab "iya". "Batalkan yang tadi" menghapusnya lagi dalam 15
  menit, dan tidak pernah menghapus catatan yang lebih lama kalau jawaban tepira tidak jelas.
- Dompet dicocokkan seperti tepira menampilkannya lalu dicatat lewat id-nya, jadi nama dengan
  spasi ganda atau lebih dari 40 karakter tetap bisa dipakai (`tepira.default_wallet` boleh
  sampai 60 karakter). Pengeluaran hari lain atau dari bonus tidak dicatat dari sini: dia
  menyarankan mencatatnya di tepira. Kalau Claude gagal setelah pengeluaran tercatat, dia tetap
  bilang pengeluaran itu sudah tercatat.
- Tidak pernah di sapaan pagi, pemberitahuan, atau notifikasi HP (sapaan pagi selalu dimulai
  di sesi baru); angkanya tidak disimpan ke ingatan kecuali kamu minta, dan tidak ada yang
  ditulis ke disk.
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
