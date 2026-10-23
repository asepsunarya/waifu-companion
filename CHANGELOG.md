# Yang baru di Cyrene

Ringkasan per versi, yang terbaru di atas. Rinciannya ada di README (bagian yang disebut di
setiap versi) dan di SPEC.md. Setelah memperbarui, **mulai ulang `waifu run`** supaya semuanya
aktif, lalu jalankan `waifu check`.

## v1.38: ulangi

- "Cyrene, ulangi" (atau "tadi bilang apa?", "apa tadi?", "sekali lagi", "maaf, apa?", "hah?")
  membuatnya mengucapkan kalimat terakhirnya di PC lagi, kata per kata dan dengan ekspresi yang
  sama, seketika dan tanpa bertanya ke otaknya (tanpa token). Dalam 8 detik sesudah dia bicara
  tidak perlu memanggil namanya.
- Yang diulang: jawabannya, pengingat dan pengumuman yang dia ucapkan sendiri, dan sapaan pagi.
  Ucapan dengan kata lain ("ulangi lagunya", "putar sekali lagi") tetap dijawab otaknya seperti
  biasa, dan "sekali lagi" hanya mengulang kata-katanya, tidak menjalankan perintahnya lagi
  ("coba sekali lagi" dan "tolong sekali lagi" dijawab otaknya, jadi perintahnya dicoba lagi).
- Pertanyaan konfirmasi (mematikan PC, mencatat pengeluaran) bisa diulang tanpa terjawab: "iya"
  sesudahnya tetap menjalankannya.
- Begitu mode tamu menyala, mati, atau berakhir sendiri, kalimat sebelumnya dilupakan, juga
  jawaban yang menyalakan atau mematikannya. Di HP kalimatnya sudah tertulis, jadi "ulangi" di
  HP dijawab otaknya seperti pesan lain. README: "Ulangi".

## v1.37: volume suaraku

- Suara Cyrene di PC punya volume sendiri: "Cyrene, kecilkan suaramu" / "keraskan suaramu"
  (20 persen sekali), "suaramu 60 persen" (10 sampai 150), atau "suaramu berapa?", lewat suara,
  `waifu chat`, atau chat di HP. Musik, game, dan volume Windows tidak ikut berubah; "kecilkan
  volume" dan "volume 30" tetap tombol volume Windows lewat perangkat musik. Di PC jawabannya
  sudah diucapkan dengan volume yang baru.
- Di atas 100 persen suaranya diperkuat dengan pembatas lembut supaya tidak pecah. Gerak mulut
  avatar tetap sama besar.
- Mode bisik (mati sampai dinyalakan): "nyalakan mode bisik" membuat suaranya paling keras 50
  persen setiap malam dari jam 22:00 sampai 06:00 (`voice.night`), sampai "matikan mode bisik".
  Sapaan pagi sesudah jam 06:00 terdengar dengan volume biasa.
- Volume dan mode bisik disimpan di `data\voice.json`, jadi tetap sesudah restart, dan dipakai
  juga oleh `waifu chat --speak` dan `waifu say`; `waifu say --volume 150 "Halo"` mencoba volume
  lain tanpa menyimpannya. Balasan di HP diputar dengan volume HP dan tidak terpengaruh.
- `waifu check` punya baris `Volume suaraku`. README: "Volume suaraku".

## v1.36: mode tamu

- "Cyrene, mode tamu" (lewat suara, `waifu chat`, atau chat di HP) menutup hal pribadi Master
  selama ada tamu: keuangan tepira, jurnal, ingatan, daftar tugas, jam main, akun game
  (status, pull, pendapatan, karakter, endgame, jadwal banner dan event, laporan mingguan, kode
  redeem), daftar jadwal, biaya API, dan melihat layar ditolak, dan dia bilang singkat bahwa itu
  ditutup dulu. Perangkat, rutinitas, lagu, cuaca, teman fokus (sesi yang sedang jalan tetap
  bisa dihentikan), promo gratis, pencarian web, obrolan biasa, dan tombol cepat di HP tetap
  jalan.
- Mulai giliran berikutnya dia memulai obrolan baru tanpa ingatannya tentang Master; saat "mode
  tamu selesai", obrolan baru lagi dan ingatannya kembali. Pertanyaan konfirmasi yang belum
  dijawab ikut terlupa.
- Sapaan pagi selama mode tamu hanya cuaca dan jadwal perangkat (tanpa tugas, teks pengingat,
  game, atau ingatan); pertanyaan malam jurnal dan laporan game mingguan dilewati. Pengingat
  yang Master pasang dan pemberitahuan lain tetap diucapkan.
- Berakhir sendiri setelah `guest_mode.max_hours` (bawaan 6 jam) atau jam 05:00, mana yang lebih
  dulu; restart `waifu run` juga mengakhirinya. Tidak ada perintah `waifu tamu`: terminal tidak
  bisa menjangkau `waifu run` yang sedang berjalan.
- `waifu check` punya baris `Mode tamu`, dengan peringatan kalau verifikasi suara mati. README:
  "Mode tamu".

## v1.35: tombol cepat di HP

- Halaman Cyrene di HP punya panel **Tombol cepat**: ketuk tombol lampu di kiri kotak pesan,
  lalu satu ketukan menyalakan, mematikan, atau membalik lampu, colokan, dan perangkat IR,
  mengatur kecerahan lampu (10, 30, 60, 100%), mengatur AC ke suhu yang sudah dipelajari (18
  sampai 28 °C, genap), atau menjalankan rutinitas ("Mode tidur"). Tombolnya dijalankan langsung
  di PC tanpa otak Cyrene (tanpa biaya API, dia tidak berbicara), dan hasilnya muncul sebentar di
  HP, juga alasannya kalau perangkatnya gagal.
- PC tidak pernah jadi tombol: tidak ada tombol mati, restart, tidur, atau nyala untuk PC, dan
  tidak ada tombol sama sekali untuk colokan PC (`powers_pc`). Itu tetap lewat suara atau chat
  dengan konfirmasi. Musik, volume, dan aplikasi juga tidak jadi tombol.
- Satu tombol berjalan sekaligus, paling banyak 10 per menit per HP; chat tetap bisa dipakai
  selagi tombol berjalan. Hanya HP yang sudah dipasangkan yang bisa menekannya.
- `phone.quick_actions` (tidak wajib) memilih dan mengurutkan perangkat dan rutinitas yang
  muncul; tanpa itu semuanya muncul. Contohnya ada di `config.example.yaml`. README: "Tombol
  cepat di HP".

## v1.34.1: perbaikan fokus dan waifu mic

- Blok fokus kembali seperti v1.30: pengingat resin, daily, mingguan, atau ekspedisi dan
  pemberitahuan GPU panas yang tertahan selama blok diucapkan saat istirahat, berapa lama pun
  bloknya. Di v1.34 pengingat itu ikut dilewati sesudah blok 30 menit, padahal tidak ada game
  yang dimainkan. Yang dilewati karena basi hanya yang ditahan game (mode game), juga kalau game
  dibuka di tengah blok; yang baru masuk sesudah game itu ditutup tetap ditunggu sampai
  istirahat.
- `waifu mic` baru: menampilkan tiap mic sekali (tanda mic yang dipakai Cyrene dan default
  Windows), mendengarkan tiap mic beberapa detik sambil Master menyebut "halo Cyrene"
  (`--seconds`, bawaan 3), lalu menulis puncak dan RMS suaranya, apakah suaranya terdengar
  sekeras dan selama yang dibutuhkan Cyrene (bunyi klik atau batuk sekali tidak dihitung), dan
  baris `audio.input_device` untuk mic terbaik. Mic yang berhenti mengirim suara di tengah
  jalan dilewati, tidak ditunggu terus. Rekamannya
  tidak disimpan, dan boleh dijalankan selagi `waifu run` jalan.
- Baris `Mikrofon` di `waifu check` menyebut mic yang dipakai dan apakah itu default Windows.
- Tidak ada pengaturan baru. README: "Pemecahan masalah" (Mic tidak terdeteksi, juga catatan
  headset Bluetooth), "Teman fokus", dan "Mode game".

## v1.34: mode game

- Mode game baru, mati dari awal: selama HSR, Genshin, atau ZZZ terbuka (dia tahu dari jam
  main), Cyrene bisa menahan pemberitahuannya sendiri sampai game terakhir ditutup
  (`game_mode.announcements: hold`), lalu mengucapkannya sekaligus sesudah "Tadi selama main
  ada kabar.". Pengingat resin, daily, mingguan, atau ekspedisi yang ditahan game lewat satu
  pengecekan HoYoLAB, dan pemberitahuan GPU panas yang ditahan game lewat sejam, dilewati karena
  sudah basi (blok fokus tanpa main game tetap menahannya sampai istirahat); yang sama persis
  hanya diucapkan sekali. Dengan `phone`, pemberitahuan yang memang dikirim ke HP tidak
  diucapkan selama main, dan sisanya ditahan. Sapaan pagi,
  laporan game mingguan, dan pertanyaan malam jurnal ikut menunggu dan tetap dilewati kalau
  sudah lewat waktunya. Pengingat dan jadwal yang Master pasang, juga pengingat tidur jam main,
  tetap berbunyi selama main.
- `game_mode.on_start` dan `on_stop` menjalankan rutinitas yang Master tulis, tanpa bicara,
  sekali saat game pertama dibuka dan sekali saat game terakhir ditutup (misalnya lampu redup,
  lalu terang lagi). Alt-tab, game yang diminimalkan, dan restart `waifu run` di tengah main
  tidak menjalankannya lagi.
- Peringatan hujan yang tertahan sampai hujannya mulai (blok fokus, obrolan, atau game) sekarang
  dilewati, tidak lagi diucapkan terlambat.
- `waifu check` menampilkan baris `Mode game`. Butuh `play_time.enabled: true` (bawaannya):
  `config.yaml` ditolak kalau mode game dinyalakan dengan jam main mati, atau kalau
  `on_start`/`on_stop` menyebut rutinitas yang tidak ada.
- Selain peringatan hujan di atas dan kalimat yang sama persis yang tertahan dua kali (sekarang
  diucapkan sekali), tanpa bagian `game_mode` tidak ada yang berubah: blok fokus tetap seperti
  v1.30. Contohnya ada di `config.example.yaml`. README: "Mode game".

## v1.33: pendapatan gacha

- Cyrene bisa menjawab "bulan ini aku dapat berapa Primogem?" atau "Stellar Jade bulan lalu
  berapa?" dari catatan bulanan HoYoLAB: Traveler's Diary (Genshin), Trailblaze Monthly Calendar
  (HSR), dan Inter-Knot monthly report (ZZZ). Dia menyebut jumlah mata uang premium, kira-kira
  berapa pull (160 per pull), dari mana paling banyak, dan untuk Genshin dan HSR juga hari ini
  dan bulan sebelumnya. Hanya kalau ditanya, sebagai informasi untuk merencanakan pull: dia tidak
  pernah menyarankan pull, belanja, atau top-up.
- Hanya bulan ini dan dua bulan sebelumnya, karena hanya itu yang disimpan HoYoLAB; bulan baru
  dihitung dari tengah malam server Asia (jam 23.00 WIB).
- `waifu gacha income [game] [--month YYYY-MM]` menampilkan hal yang sama di terminal, juga
  saat `gacha.enabled: false`.
- Laporan game mingguan ikut menyebut pendapatan bulan ini per game, dan `waifu gacha report`
  menambah baris `Pendapatan bulan ini: ...`.
- Kedua persona bawaan mendapat baris `gacha_income` di bagian "Game HoYoverse Master". Kalau
  kamu memakai persona buatan sendiri, salin baris itu dari `config/persona.md`.
- Tidak ada pengaturan baru; mulai ulang `waifu run`. README: "Pendapatan gacha bulanan".

## v1.32: mode tidur

- Langkah rutinitas sekarang bisa ditunda dengan `after_minutes`, jadi "Cyrene, mode tidur" bisa
  mematikan lampu, menyetel AC ke 26, lalu mematikan AC tiga jam kemudian. Dia menyebut jamnya
  ("AC akan mati jam satu nanti"), dan langkah itu masuk jadwal: tetap ada walau `waifu run`
  dijalankan ulang, terlihat di `waifu schedule list` sebagai `perangkat ac off (rutinitas
  tidur)`, dan bisa dibatalkan ("Cyrene, batalkan AC mati nanti" atau
  `waifu schedule cancel <id>`). Saat jamnya tiba, AC dimatikan diam-diam seperti jadwal
  perangkat lain; dia hanya bicara kalau gagal.
- Menjalankan rutinitas yang sama lagi menggantikan langkah yang ditunda dari jalannya yang
  sebelumnya, jadi AC tidak dimatikan dua kali. Jadwal yang Master buat sendiri tidak disentuh.
- `after_minutes` adalah bilangan bulat menit, 1-1440. `config.yaml` ditolak saat start untuk
  langkah yang ditunda pada PC atau colokan PC (seperti langkah biasa), `play_pause` atau `mute`
  musik yang ditunda, dan langkah yang ditunda saat `schedule.enabled: false`.
- PC dan `waifu run` harus tetap menyala sampai jamnya: atur Windows supaya tidak tidur sendiri
  sebelum itu.
- `waifu routine list`, `waifu routine run`, dan `waifu check` menampilkan jedanya (`ac off
  setelah 3 jam`). Contoh rutinitas `tidur` di `config.example.yaml` sekarang ikut mematikan AC
  3 jam kemudian; rutinitas yang sudah ada di `config.yaml` tidak berubah. Kalau rutinitas
  `tidur` sudah ada, tambahkan langkah `- {device: ac, command: off, after_minutes: 180}` di
  akhirnya (lihat Mode tidur), lalu mulai ulang `waifu run`.
- Turun lagi ke versi sebelum v1.32 selagi ada langkah yang ditunda di jadwal membuat versi lama
  menganggap `data\schedule.json` rusak dan memindahkannya ke samping (`.corrupt-...`); batalkan
  dulu langkah itu sebelum turun versi.
- README: "Mode tidur" (di bagian "Jadwal, pengingat, dan rutinitas").

## v1.31.1: pertanyaan yang tertahan

- Pertanyaan malam jurnal yang tertahan sampai tengah malam (blok fokus, obrolan lewat HP, atau
  obrolan dengannya) dilewati malam itu, jadi dia tidak lagi bertanya tentang "hari ini" di
  tanggal yang baru.
- Begitu juga sapaan pagi yang tertahan sampai `briefing.until` dan laporan game mingguan yang
  tertahan sampai tengah malam: dilewati hari itu, tidak diucapkan terlambat atau dikirim ke HP.
  Log `waifu run` mencatatnya dengan `Not saying a request held past`.
- Jawaban pertanyaan malam yang diucapkan tepat sesudah tengah malam dicatat di tanggal dia
  bertanya, bukan tanggal yang baru, asal dijawab dalam sekitar 10 menit sejak pertanyaannya.
- Tidak ada pengaturan baru. README: "Sapaan pagi dan ringkasan harian", "Laporan game
  mingguan", dan "Jurnal harian".

## v1.31: jurnal harian

- Cyrene bisa menyimpan jurnal harian Master: "catat di jurnal: hari ini laporannya selesai"
  mencatatnya di tanggal hari ini dengan kata-kata Master sendiri, beberapa catatan sehari. Lewat
  tengah malam, cerita tentang hari yang baru lewat dicatat di tanggal kemarin.
- "Kemarin aku nulis apa di jurnal?", "jurnal minggu ini apa aja?", atau "jurnal tanggal 12
  Oktober" dirangkum singkat; dia membacakan catatan kata per kata hanya kalau diminta.
- Hanya kalau Master minta: jurnal tidak pernah disebut di sapaan pagi, laporan game mingguan,
  atau notifikasi HP, dan rahasia seperti kata sandi atau nomor kartu selalu ditolak.
- Pertanyaan malam pilihan, mati dari awal: isi `journal.ask_at` (misalnya `21:30`), dan sekali
  sehari mulai jam itu sampai tengah malam dia bertanya bagaimana hari Master. Jawab langsung
  tanpa memanggil namanya, dan jawabannya dicatat; kalau tidak dijawab, tidak ada yang dicatat dan
  dia tidak bertanya lagi hari itu. Tidak dikirim ke HP, dan menunggu istirahat selama blok fokus.
- Selagi Master mengobrol lewat Cyrene di HP, sapaan pagi, laporan mingguan, dan pertanyaan malam
  menunggu sampai sekitar 10 menit setelah pesan HP terakhir, supaya obrolan di HP tidak terhapus.
- Menghapus hanya lewat terminal: `waifu jurnal forget <tanggal> --yes`. `waifu jurnal` juga
  menampilkan hari yang ada catatannya, catatan satu hari (`show`), dan menambah catatan (`add`),
  dan `waifu check` menyebut jurnal. Bagian baru `journal` di `config.yaml` opsional. Catatannya
  di `data\journal.json`, hanya di PC ini.
- README: "Jurnal harian".

## v1.30: teman fokus

- Cyrene bisa menemani Master fokus belajar atau kerja dengan cara pomodoro, selama `waifu run`:
  "temani aku fokus 25 menit", "pomodoro empat kali", atau "fokus 50 menit, istirahat 10, tiga
  kali". "Sisa berapa menit?", "hari ini sudah fokus berapa lama?", dan "berhenti fokusnya" juga
  bisa. Blok 5-120 menit, istirahat 1-60 menit, sampai 8 blok.
- Di akhir blok dia bilang "Sayang, dua puluh lima menit selesai. Istirahat lima menit dulu ya.",
  di akhir istirahat "Yuk, lanjut fokus lagi.", dan di akhir sesi memuji dengan totalnya.
- Selama blok fokus dia diam: peringatan hujan, kabar versi baru, pengingat tidur, pemberitahuan
  game, kode, PC, promo, dan anggaran, juga sapaan pagi dan laporan mingguan, menunggu sampai
  istirahat lalu diucapkan sekaligus; sapaan "sudah kembali" dari maskot dilewati. Pengingat dan
  jadwal yang Master pasang sendiri tetap berbunyi tepat waktu, dan Master tetap bisa
  memanggilnya seperti biasa.
- Sesinya hanya ada di `waifu run` yang sedang berjalan: restart mengakhirinya. Kalau dihentikan
  dengan Ctrl+C, bagian blok yang sudah berjalan minimal satu menit tetap dicatat; menutup jendela
  "Cyrene", sign out, atau mematikan PC di tengah blok membuang bagian blok itu (blok yang sudah
  selesai tetap tercatat).
- `waifu focus` menampilkan waktu fokus hari ini dan minggu ini, dan `waifu check` menyebut teman
  fokus. Bagian baru `focus` di `config.yaml` opsional: tanpa itu sudah menyala, tanpa kiriman ke
  HP (`focus.push: true` untuk mengirim kalimatnya ke HP). Catatannya di `data\focus.json`, hanya
  di PC ini.
- README: "Teman fokus".

## v1.29: jam main

- Selama `waifu run`, Cyrene mencatat berapa lama HSR, Genshin, dan ZZZ terbuka, dari nama
  programnya di daftar proses Windows; dia tidak pernah membuka atau menyentuh proses game, jadi
  aman untuk anti-cheat. "Hari ini aku main berapa lama?" atau "minggu ini Genshin berapa jam?"
  dijawab dari catatannya, singkat, dan hanya kalau ditanya.
- Yang dihitung adalah waktu game terbuka (diminimalkan atau ditinggal ikut terhitung), hanya
  selama `waifu run` menyala. Sleep tidak dihitung, restart Cyrene di tengah main tidak memecah
  sesi, dan hari dihitung WIB.
- Pengingat tidur pilihan, mati dari awal: isi `play_time.bedtime` (misalnya `23:30`), dan lewat
  jam itu dia bilang sekali semalam kalau game masih terbuka: "Sayang, sudah lewat jam setengah
  dua belas, jangan terlalu malam ya." Tidak pernah memotong obrolan dan tidak dikirim ke HP.
  Game yang baru dibuka mulai jam 04.00 (daily sesudah bangun) tidak diingatkan.
- Laporan game mingguan ikut menyebut jam main minggu itu per game, juga `waifu gacha report`.
- `waifu playtime [today|yesterday|week|month]` menampilkan catatannya, dan `waifu check`
  menyebut game yang dicatat. Bagian baru `play_time` di `config.yaml` opsional: tanpa itu
  pencatatan sudah menyala. Catatannya di `data\playtime.json`, hanya di PC ini.
- Nama program Genshin (`GenshinImpact.exe`) dan ZZZ (`ZenlessZoneZero.exe`) belum pernah
  terlihat langsung di PC ini (HSR sudah): kalau keduanya tidak tercatat, lihat Task Manager →
  Details dan tulis namanya di `play_time.games`.
- Privasi: jam main tidak punya pemberitahuan HP sendiri, tapi kalimat laporan game mingguan
  yang menyebutnya ikut ke `ntfy.sh` kalau notifikasi HP dan `weekly_report.push` menyala;
  `weekly_report.push: false` membuat laporannya tetap di PC.
- README: "Jam main".

## v1.28: suara cadangan offline

- Kalau edge-tts gagal (tanpa internet, atau ditolak Microsoft seperti 403 pada 16 Oktober),
  Cyrene tidak diam lagi: bagian itu diucapkan suara bawaan Windows yang jalan offline, lewat
  suara anime kalau menyala. Tidak ada paket baru: Windows PowerShell bawaan dan speech API
  Windows yang bekerja, sekitar 0,4 detik per bagian.
- Bagian Indonesia memakai suara Indonesia Windows (Microsoft Andika), bagian Jepang suara
  Jepang (perempuan lebih dulu). **Tambahkan suaranya dulu, gratis:** Settings → Time &
  language → Speech → Add voices, pilih Indonesian dan Japanese, lalu mulai ulang `waifu run`.
  Tanpa suara itu, bahasa itu tetap diam saat edge-tts gagal, seperti dulu (teksnya tampil).
- Setelah edge-tts gagal, bahasa itu memakai suara Windows selama 10 menit tanpa menunggu
  edge-tts gagal lagi di setiap jawaban, lalu edge-tts dicoba lagi; log mencatatnya sekali
  per gangguan dan sekali saat edge-tts pulih.
- Dengan suara anime, suara Windows dinaikkan dengan nadanya sendiri per jenis suara
  (`tts.fallback.rvc_pitch`: perempuan +11, laki-laki +18), karena suara Windows jauh lebih
  rendah dari edge-tts.
- `waifu say --fallback "Halo Master"` memperdengarkan suara cadangan (dengan `--raw` tanpa
  suara anime), `waifu voices` menampilkan suara Windows per bahasa, dan `waifu check`
  menyebut suara cadangan tiap bahasa atau tempat menambahkannya. Bagian baru `tts.fallback`
  di `config.yaml` opsional: tanpa itu semuanya sudah menyala.
- README: "Suara cadangan offline".

## v1.27.1: jam HSR

- Jam warp, event, dan jendela endgame HSR kini sama dengan timer di game: warp yang berakhir
  jam 11.00 WIB tidak lagi tertulis `berakhir 10:59`, di `waifu gacha hsr events`, jawaban
  Cyrene, maupun laporan mingguan. Genshin dan ZZZ sudah begitu sejak v1.23.
- Musim endgame HSR (Memory of Chaos, Pure Fiction, Apocalyptic Shadow, Anomaly Arbitration)
  kini berakhir jam 03.00 WIB, sama dengan jendelanya di kalender, bukan `02:59`, di
  `waifu gacha hsr endgame`, jawaban Cyrene, maupun laporan mingguan.

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
