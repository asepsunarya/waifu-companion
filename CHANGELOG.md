# Yang baru di Cyrene

Ringkasan per versi, yang terbaru di atas. Rinciannya ada di README (bagian yang disebut di
setiap versi) dan di SPEC.md. Setelah memperbarui, **mulai ulang `waifu run`** supaya semuanya
aktif, lalu jalankan `waifu check`.

## v1.52: respons instan

- Kalau otaknya belum menjawab kalimatmu setelah 1,2 detik (`nod.after_ms`), Cyrene bilang satu
  kalimat pendek dulu dengan suaranya sendiri ("Hmm, bentar ya…", "Sebentar, kucek dulu ya.", dan
  empat lainnya, tidak pernah sama dua kali berturut-turut), lalu jawabannya menyusul begitu
  selesai, tanpa jeda tambahan. Jawaban yang cepat datang tanpa kalimat itu, seperti biasa.
- Kalimat-kalimat itu disiapkan sekali di latar belakang setelah dia siap dan disimpan di
  memori, jadi tidak pernah menunggu edge-tts atau suara anime. Yang saat disiapkan keluar dengan
  suara cadangan Windows atau tanpa suara anime tidak dipakai sampai `waifu run` dijalankan ulang.
- Hanya untuk kalimatmu sendiri yang dijawab otak: tidak untuk "ulangi", alarm, "cara baca",
  sapaan pagi, pertanyaan jurnal, laporan mingguan, pesan dari HP, atau `waifu chat`. "Ulangi"
  mengulang jawabannya, bukan kalimat itu.
- Matikan dengan `nod.enabled: false`. Tidak ada tool baru.
- README: "Respons instan".

## v1.51: panduan setup

- `waifu setup` memandumu mengisi yang masih contoh di `config.yaml`, langsung di terminal, lalu
  menuliskannya sendiri: **mic** (tiap mic didengarkan 3 detik selagi kamu menyebut "halo
  Cyrene", lalu pilih nomornya, atau beberapa berurutan sebagai cadangan), **perangkat Tuya**
  (wizard tinytuya dijalankan di terminalmu, kamu mengetik API Key dan API Secret sendiri ke
  wizard, lalu tiap lampu, colokan, dan IR blaster Tuya dicocokkan menurut namanya dan
  `device_id`, `local_key`, `address`, `version` ditulis), **IR blaster** (alamat IP blaster
  Broadlink untuk semua perangkat di blaster itu, lalu perintah `waifu learn-ir` yang masih
  perlu), dan **notifikasi HP** (topik ntfy acak, langkah berlangganan di aplikasi ntfy, dan
  notifikasi tes kalau kamu mau). Tanpa langkah, keempatnya berurutan lalu `waifu check`; atau
  satu saja: `waifu setup mic`, `tuya`, `ir`, `notify`.
- Setiap langkah bisa dilewati (`n`, atau `l` saat diminta nomor), dan menjalankannya lagi aman:
  `device_id` yang sudah asli dan topik ntfy yang sudah ada hanya diganti kalau kamu minta, dan
  nilai yang sudah sama tidak ditulis ulang. Ctrl+C menghentikan setup, juga di dalam wizard.
- Kalau wizard tinytuya tidak mendapat daftar perangkat dari cloud Tuya (misalnya API Key
  salah, atau layanan IoT Core project-mu habis setelah bulan pertama), tidak ada yang diimpor,
  juga dari `devices.json` lama, dan Cyrene menjelaskan arti pesan Inggris wizard itu. Langkah
  Tuya juga menyebut dua penghalang yang paling sering: tab Devices kosong (data center salah)
  dan layanan IoT Core yang perlu diperpanjang.
- Di akhir ada ringkasan tiap langkah dan **Langkah berikutnya** (misalnya `waifu enroll`
  setelah mic diganti, `waifu learn-ir ac --missing`, `waifu setup notify`), sebelum
  `waifu check`.
- Hanya kunci yang disebut yang diubah; komentar dan format `config.yaml` tetap. Isi lamanya
  disalin dulu ke `data\config.yaml.bak-<tanggal>-<jam>`, dan kalau setelah ditulis
  `config.yaml` tidak terbaca persis seperti yang dimaksud, isi lama dikembalikan. Bentuk YAML
  yang tidak bisa diubah per baris tidak disentuh: setup menampilkan potongan untuk ditempel
  sendiri.
- Local key tidak pernah ditampilkan atau dicatat. Wizard menulis filenya ke folder proyek (semua
  ada di `.gitignore`); setelah impor Cyrene menawarkan menghapus `tinytuya.json` dan
  `tuya-raw.json`, yang berisi API Key dan API Secret akun Tuya-mu, dan kalau setup berhenti
  sebelum itu, dia bilang keduanya masih ada. File sementara saat `config.yaml` ditulis juga ada
  di `.gitignore`.
- `waifu check` menandai perangkat Tuya yang masih `isi_dari_tinytuya_wizard` ("Jalankan:
  waifu setup tuya") dan IR blaster di alamat contoh `192.168.1.50` ("waifu setup ir"; abaikan
  kalau blastermu memang di alamat itu).
- Perubahan dipakai setelah `waifu run` dijalankan ulang. Tidak ada tool baru.
- README: "Setup pertama", "Lampu dan colokan Tuya".

## v1.50: sebelum reset

- Mulai `daily_time` (bawaan 20:00), Cyrene menyebut sekali sehari per game apa yang mau habis
  sebelum kamu sempat: musim endgame HSR (Memory of Chaos, Pure Fiction, Apocalyptic Shadow,
  Anomaly Arbitration) dan Spiral Abyss yang berakhir dalam 3 hari dan belum maksimal (sekali per
  musim), lalu paling banyak 2 event HSR atau ZZZ yang berakhir dalam 2 hari dan progresnya
  belum penuh: "Master, Memory of Chaos di HSR berakhir sekitar 50 jam lagi, baru dapat 30 dari
  36. Event Gift of Odyssey berakhir sekitar 20 jam lagi, baru 3 dari 7."
- Kalau kamu menutup game antara jam 22.00 dan 03.00 dan staminanya akan penuh sebelum pagi
  (`briefing.time`, bawaan 07.00), dia bilang sekali, setelah semua game tertutup: "Master,
  Trailblaze Power di HSR penuh jam 04.10, sebelum kamu bangun." Pengingat "sudah penuh" game itu
  lalu menunggu sampai pagi, tidak lagi berbunyi tengah malam di kamar kosong; selama kamu
  membuka game lagi, pengingatnya berbunyi seperti biasa. Stamina yang penuh dalam
  `stamina_minutes` (bawaan 60 menit) tetap disebut pengingat stamina biasa. Butuh jam main
  menyala (bawaan).
- Untuk itu kalender event tiap game dibaca sekali sehari, kira-kira 4 permintaan HoYoLAB
  tambahan sehari, disimpan sejam bersama jawaban "event apa yang mau habis?". Yang tidak
  dicatat HoYoLAB tidak disebut: event Genshin, Imaginarium Theater, Stygian Onslaught, ZZZ
  endgame, dan musim yang belum kamu mainkan.
- Selama mode tamu menyala ketiganya diam. Mode game menahan baris musim dan event seperti
  pengingat daily.
- Bisa dimatikan dengan `gacha.reminders.planner: false`. Tidak ada tool baru.
- README: "Pengingat" di bagian teman main gacha.

## v1.49.1: konfirmasi kamus

- Mengajari cara baca lewat suara sekarang ditanyakan dulu: "Cyrene, cara baca Acheron itu A Ke
  Ron" dijawab "Jadi Acheron kubaca A Ke Ron? Bilang iya kalau benar.", dan baru dicatat kalau
  kalimatmu berikutnya, dalam satu menit, hanya berisi iya ("iya", "bener", "oke, catat aja",
  "iya bener banget"). Jawaban lain apa pun membatalkannya tanpa dibahas dan dijawab seperti
  biasa, dan begitu juga pengingat atau pengumuman yang dia ucapkan sebelum kamu menjawab;
  namanya saja ("Cyrene", lalu "iya") tidak membatalkan. Sebelumnya kalimat biasa seperti
  "cara baca jam itu begini" langsung tercatat, lalu setiap "jam" yang dia ucapkan (misalnya
  "penuh jam 04.10") terdengar "begini".
- Lupakan cara baca lewat suara, tanpa otak: "Cyrene, lupakan cara baca Acheron" (atau "hapus
  cara baca untuk Acheron sekarang"). Aturan bawaan (HSR, ZZZ, ...) tidak bisa dilupakan, hanya
  diganti.
- Lebih banyak kalimat komentar diteruskan ke otak tanpa ditanyakan: yang memuat begini, dengan,
  pakai, penting, dulu, biasa, sama, lain, pelan, cepat, ribet, nanti, dan sejenisnya, juga
  yang diulang dengan tanda hubung ("pelan-pelan").
- Kalau sudah ada aturan yang tidak sengaja tercatat, cek dengan `waifu lafal list` lalu hapus
  dengan "lupakan cara baca ..." atau `waifu lafal remove`.
- Tidak ada tool baru.
- README: "Kamus Cyrene".

## v1.49: kamus cyrene

- Whisper sekarang menunggu kata-kata duniamu sendiri: namanya dan kata panggilannya, nama dan
  alias perangkat, rutinitas, aplikasi PC, game gacha yang menyala, dan nama karakter milikmu
  setelah kamu menanyakan karakter game itu (paling banyak 5 per game, bergiliran antar game,
  tanpa membuka HoYoLAB hanya untuk itu). Daftarnya pendek (paling banyak 40 kata) dan urut dari
  yang paling penting, alias perangkat paling akhir, jadi namanya, perangkat, dan nama karakter
  lebih sering tertulis benar walau TV menyala.
- Suara Indonesia (edge-tts dan suara cadangan Windows) membaca **HSR** "Ha Es Er", **ZZZ** "Zet
  Zet Zet", **HoYoLAB** "Hoyolab", dan **HoYoverse** "Hoyovers". Suara Jepang, teks di HP,
  riwayat obrolan, dan log tidak diubah; "Zzz..." (suara tidurnya) tetap.
- Ajari cara baca kata lain lewat suara, tanpa otak: "Cyrene, cara baca Acheron itu A Ke Ron"
  dijawab "Oke Master. Acheron mulai sekarang kubaca A Ke Ron.", dengan Acheron masih dibaca
  cara lama supaya kamu dengar kata mana yang dia tangkap. Kalimat tanya ("cara baca HSR itu
  gimana?"), komentar ("cara baca HSR itu salah", "cara baca kamu itu aneh"), dan obrolan lewat
  HP tetap ke otak seperti biasa dan tidak dicatat.
- Perintah baru `waifu lafal add|list|remove` untuk melihat, menambah, dan menghapus cara baca;
  aturan bawaan bisa diganti, tidak bisa dihapus. Disimpan di `data\tts_lexicon.json`
  (`tts.lexicon_path`) dan dipakai `waifu run` yang sedang berjalan mulai kalimat berikutnya.
- Tidak ada tool baru.
- README: "Kamus Cyrene".

## v1.48: hotkey global

- Empat tombol pintas yang tetap sampai ke Cyrene walau game layar penuh sedang di depan
  (hanya Windows, selama `waifu run` jalan): **Ctrl+Alt+C** langsung mendengarkan tanpa
  dipanggil namanya, seperti klik dua kali avatar; **Ctrl+Alt+X** memotong ucapannya;
  **Ctrl+Alt+R** mengulang ucapan terakhirnya; **Ctrl+Alt+Q** berhenti mendengarkan.
- Selama Ctrl+Alt+Q tidak ada suara mic yang sampai ke Whisper, jadi voice chat game tidak
  membangunkannya, tapi pengingat, alarm, dan kabar tetap diucapkan, dan selagi alarm berbunyi
  dia tetap mendengar "aku sudah bangun". Dia bilang sampai jam berapa, avatar menampilkan
  **Tidak mendengarkan**, dan dia mendengarkan lagi dengan Ctrl+Alt+Q, Ctrl+Alt+C, klik dua
  kali, tombol **Dengar lagi** di tombol cepat HP dan `waifu tombol`, atau sendiri sesudah 2 jam.
  Avatar di tab browser yang tetap terbuka saat `waifu run` dijalankan ulang tidak lagi
  menampilkan **Tidak mendengarkan** yang sudah lewat.
- Tiap tombol bisa diganti (A-Z, 0-9, F1-F12) atau dimatikan di bagian baru `hotkeys`; semuanya
  sudah menyala tanpa ditulis. Kombinasi yang sudah dipakai program lain disebut di log dan di
  baris baru `Hotkey global` di `waifu check`. Baris itu menyebut kombinasi yang benar-benar
  dipegang `waifu run`, memperingatkan kalau tidak ada satu pun, dan mengingatkan bahwa
  `config.yaml` yang diubah baru dipakai setelah `waifu run` dijalankan ulang.
- Selama `waifu run` jalan, keempat kombinasi itu tidak sampai ke program lain (misalnya
  Ctrl+Alt+C © dan Ctrl+Alt+R ® di Word, Ctrl+Alt+C di PyCharm); kalau kamu memakainya, ganti
  atau matikan tombolnya. Di keyboard ber-AltGr, Ctrl+Alt sama dengan AltGr: ganti tombol yang
  kamu pakai untuk huruf khusus.
- Game dengan anti-cheat atau yang berjalan sebagai administrator bisa menelan semua
  kombinasinya selama game itu aktif: panggil namanya untuk bicara, dan supaya voice chat tidak
  membangunkannya, tekan Ctrl+Alt+Q sebelum masuk game lalu akhiri dengan **Dengar lagi** di HP.
  Ctrl+Alt+Q selagi dia bicara baru dikonfirmasi sesudah ucapannya selesai; Ctrl+Alt+X
  memotongnya lebih dulu.
- Tombol lain yang kamu ketik tidak pernah terlihat oleh Cyrene. Tidak ada tool baru.
- README: "Hotkey global".

## v1.47: diagnosa suara

- Halaman HP punya tombol **Suara**: mic yang dipakai Cyrene dan host API-nya, mic default
  Windows, bar level suara dengan garis batas deteksi bicaranya, dan 50 ucapan terakhir dengan
  teks yang ditangkap, skor kecocokan suaramu dibanding batasnya, lama Whisper, otak, dan
  suaranya, berwarna sesuai yang dia lakukan (dijawab, tanpa nama, bukan suaramu, tak ada kata,
  terlalu pendek). Diperbarui tiap 1,5 detik selama terbuka; disembunyikan selama mode tamu.
- Dari halaman itu mic bisa diganti tanpa menjalankan ulang `waifu run` (mic yang tidak bisa
  dibuka tidak pernah dipakai), dan ambang kecocokan suara (0,30 sampai 0,70) serta ambang dan
  margin VAD bisa disetel, dengan **Reset ke default**. Pilihannya disimpan di
  `data\voice_override.json` (`audio.override_path`), `config.yaml` tidak diubah. **Simpan**
  hanya menyimpan angka yang diubah, jadi yang lain tetap mengikuti `config.yaml`. Hasil ganti
  mic muncul tepat di bawah pilihannya, juga kalau gantinya menunggu dia selesai bicara; daftar
  **Yang terdengar** ada di atas **Setelan**, dan saat masih kosong menjelaskan yang harus dicoba;
  mic yang hanya mengirim sunyi total tertulis "Sunyi total" dengan kemungkinan sebabnya.
- `audio.input_device` boleh berisi beberapa mic berurutan. Kalau mic yang dipakai terputus atau
  hanya mengirim sunyi total, dia pindah ke mic berikutnya (terakhir mic default Windows) dan
  bilang sekali mic mana yang dipakai sekarang; headset yang tersambung lagi dipakai lagi. Mic
  yang gagal tidak lagi menghentikan `waifu run` dengan "Terjadi kesalahan tak terduga": dia
  tetap jalan dan mencoba lagi tiap 5 detik. Mic dengan nomor dari `waifu mics` tidak dipakai
  lagi otomatis sesudah kembali; pakai nama dari `waifu mic`.
- Mic yang hanya mengirim sunyi total tidak dicoba lagi sampai daftar mic Windows berubah, jadi
  dia tidak bolak-balik di antara dua mic. Kalau semua mic begitu (di-mute, atau izin mikrofon
  Windows untuk aplikasi desktop mati), dia tetap di satu mic dan bilang sekali; begitu mic-nya
  tidak di-mute lagi, dia langsung mendengar. Headset Bluetooth yang muncul di Windows sebelum
  siap dibuka dicoba lagi beberapa kali. Kalimat ganti mic yang tertahan teman fokus atau mode
  game dilewati sesudah semenit.
- "Cyrene, tadi kamu denger apa?" dijawab tanpa otak: ucapan terakhir yang dia dengar dan
  kenapa dia menjawab atau diam.
- `waifu check`, `waifu mic`, `waifu enroll`, dan `waifu speaker-test` memakai urutan mic yang
  sama (mic pilihan HP dulu); baris `Mikrofon` di `waifu check` menyebut pilihan HP, urutan
  daftar, dan setelan suara dari HP. `waifu speaker-test` menilai dengan ambang dan VAD dari HP,
  dan selama ada pilihan HP, `waifu enroll` dan `waifu mic` menyarankan halaman Suara, bukan
  `config.yaml`.
- Rekaman tidak pernah disimpan; catatan ucapan hanya di memori. Tidak ada tool baru.
- README: "Diagnosa suara", dan "Pemecahan masalah" untuk mic sekarang mulai dari halaman Suara.

## v1.46: alarm bangun

- "Cyrene, bangunkan aku jam 6", "alarm jam 5.30 besok buat berangkat kerja", atau "alarm tiap
  hari kerja jam 6" memasang alarm. Pada jamnya dia bilang "Master, bangun! Sudah jam 06.00.",
  lalu mengulanginya setiap menit, makin keras sampai 150 persen, dan tidak pernah berbisik
  walau mode bisik menyala. Juga selama teman fokus atau mode game.
- "Aku sudah bangun", "udah bangun", "matikan alarm", atau "stop alarm" mematikannya tanpa
  memanggil namanya; "tunda", "snooze", "tunda 10 menit", atau "lima menit lagi" menundanya.
  Hanya kalimat itu persis: "matikan lampu" atau "stop" saja tidak menyentuh alarm. Alarm
  berhenti sendiri setelah 10 menit.
- Selagi berbunyi, tombol cepat di HP dan `waifu tombol` punya grup **Alarm** paling atas
  (**Matikan alarm**, **Tunda 5 menit**); keduanya sekarang membaca daftar tombolnya lagi
  sendiri (HP: saat halaman tampil lagi, saat panel dibuka, dan setiap 15 detik selama grup
  **Alarm** tampil; PC: setiap 15 detik), dan keduanya langsung sesudah tombol alarm ditekan,
  jadi grupnya hilang begitu alarmnya berhenti.
- Dengan notifikasi HP, bunyi pertama juga dikirim ke HP dan membuka halaman Cyrene di HP.
- **Hanya selama `waifu run` jalan dan PC menyala.** Alarm yang terlewat karena PC mati atau
  sleep tidak dibunyikan terlambat, hanya dikirim ke HP ("Alarm jam 06.00 terlewat.").
- "Alarmku apa aja?" dan "batalkan alarm jam 6" lewat tool baru `alarm`; `waifu schedule list`
  menampilkan alarm, `waifu check` punya baris `Alarm`. Pengaturan baru `alarm` (jarak bunyi,
  lama berbunyi, lama tunda, kiriman ke HP), semuanya sudah menyala tanpa ditulis.
- Di mode tamu alarm tetap berbunyi; daftar dan membatalkannya ditutup.
- Turun lagi ke versi sebelum v1.46 selagi ada alarm di jadwal membuat versi lama menganggap
  `data\schedule.json` rusak dan memindahkannya ke samping (`.corrupt-...`); batalkan dulu
  alarmnya (`waifu schedule cancel <id>`) sebelum turun versi.
- README: "Alarm bangun".

## v1.45: rekap hari ini

- "Cyrene, hari ini aku ngapain aja?", "rekap kemarin dong", atau "tanggal 20 aku ngapain?"
  dijawab dalam dua sampai empat kalimat hangat dari catatannya sendiri di PC ini: tugas yang
  selesai dan yang masih menunggu, berapa lama fokus, berapa lama game terbuka, pull gacha hari
  itu dengan bintang 5-nya, kata Jepang yang dipelajari, berapa catatan jurnal (tidak pernah
  isinya), dan jadwal hari itu. Bisa hari ini, kemarin, atau tanggal lain sampai 30 hari ke
  belakang.
- Tanpa internet dan tanpa pengaturan baru: tiap bagian ikut sakelar fiturnya sendiri. Pull
  dibaca dari riwayat yang terakhir disimpan, tidak diperbarui. Uang tidak pernah disebut, dan
  jam main hanya kabar, tidak pernah dinilai.
- Cyrene tidak mencatat pengingat mana yang sudah diucapkan, jadi bagian jadwal menyebut yang
  dijadwalkan hari itu; pengingat sekali yang sudah lewat tidak ikut.
- `waifu rekap`, `waifu rekap kemarin`, atau `waifu rekap 2026-10-20` menampilkan hal yang sama
  tanpa otak.
- Ditutup di mode tamu. README: "Rekap hari ini".
- Juga: `waifu cuaca` dan `waifu briefing` menulis satu suhu (`20°C`) kalau suhu terendah dan
  tertingginya sama, bukan `20-20°C`, dan Cyrene menyebutnya sekali.

## v1.44: cuaca beberapa hari

- Cyrene menjawab cuaca hari-hari berikutnya: "Cyrene, besok cuaca gimana?", "nanti sore hujan
  nggak?", "nanti malam?", "akhir pekan hujan nggak?", "Jumat cuacanya gimana?". Bisa sisa hari
  ini, satu bagian hari (pagi, siang, sore, malam), besok, akhir pekan, atau tanggal lain sampai
  enam hari lagi, dengan kondisi, peluang hujan, dan suhunya. "Cuaca hari ini gimana?" tetap
  dijawab dari ringkasan hari (dari prakiraan sisa hari ini kalau sapaan paginya dimatikan), dan
  sapaan paginya tidak berubah.
- Tempatnya kota yang sama dengan sapaan pagi dan peringatan hujan (`briefing.city`), juga saat
  sapaan paginya dimatikan; tidak ada pengaturan baru. Setiap pertanyaan butuh internet dan
  menunggu Open-Meteo sebentar (paling lama 8 detik).
- `waifu cuaca` menampilkan sisa hari ini per bagian hari; `waifu cuaca besok`,
  `waifu cuaca akhir-pekan`, atau `waifu cuaca 2026-10-30` hari lain, tanpa otak.
- Tetap bisa ditanyakan di mode tamu. README: "Cuaca beberapa hari".

## v1.43: jadwal mingguan, bulanan, tahunan

- Jadwal sekarang bisa berulang tiap minggu, tiap bulan, dan tiap tahun: "Cyrene, tiap Senin
  jam 7 ingatkan buang sampah.", "Cyrene, tiap tanggal 20 ingatkan bayar listrik.", "Cyrene,
  ingatkan ulang tahun Ibu tiap 5 Mei." Tanpa jam, ulang tahun dan tagihan diingatkan jam 08.00
  pagi itu ("Master, hari ini ulang tahun Ibu!"). Tanggal yang sudah lewat tahun ini mulai
  tahun depan.
- Tanggal 29, 30, atau 31 jalan di hari terakhir bulan yang lebih pendek (30 November, 28
  Februari), lalu kembali ke tanggalnya; ulang tahun 29 Februari diingatkan 28 Februari di tahun
  biasa.
- "Jadwal bulan ini apa?" dan "ulang tahun siapa bulan ini?" dijawab dari daftar jadwal.
  `waifu schedule list` menulis `tiap Senin`, `tiap tanggal 20`, dan `tiap 5 Mei`.
- Pengingat bulanan atau tahunan yang terlewat karena PC atau `waifu run` mati sekarang
  diucapkan begitu dia jalan lagi, seperti pengingat sekali, tidak hanya dikirim ke HP.
- Jadwal yang sudah ada tidak berubah. Turun lagi ke versi sebelum v1.43 selagi ada jadwal
  mingguan, bulanan, atau tahunan membuat versi lama menganggap `data\schedule.json` rusak dan
  memindahkannya ke samping (`.corrupt-...`); batalkan dulu jadwal itu sebelum turun versi.
- README: "Mingguan, bulanan, dan tahunan" (di bagian "Jadwal, pengingat, dan rutinitas").

## v1.42: tombol cepat di PC

- `waifu tombol` membuka tombol cepat HP di browser PC ini: lampu, AC, colokan, dan rutinitas,
  sekali klik, tanpa bicara ke Cyrene dan tanpa biaya API. Tombolnya sama dengan di HP
  (`phone.quick_actions` memilihnya untuk keduanya); tidak perlu `waifu run`, avatar, atau
  Tailscale, dan tidak ada pengaturan baru. `--dry-run` memakai perangkat simulasi.
- Langkah rutinitas yang ditunda tetap butuh `waifu run` yang menyala pada jamnya; kalau ada
  tombol seperti itu, `waifu tombol` mengingatkannya saat dibuka.
- Jendela terminalnya tetap terbuka selama halaman dipakai; Ctrl+C menutupnya. Alamatnya baru
  setiap kali, dengan token acak, dan hanya di `127.0.0.1`: situs lain di browser yang sama dan
  komputer lain tidak bisa menekan tombolnya.
- PC dan colokan PC tetap tidak pernah jadi tombol; paling banyak 10 tombol per menit, satu per
  satu, seperti di HP. `waifu check` punya baris `Tombol cepat di PC`. README: "Tombol cepat di
  PC".

## v1.41: waktu sholat

- Mati sampai dinyalakan (`prayer.enabled: true`); selama mati, tidak ada yang dia ucapkan soal
  sholat. "Jadwal sholat hari ini?", "Maghrib jam berapa?", atau "jadwal sholat besok" dijawab
  dengan imsak, subuh, terbit, dzuhur, ashar, maghrib, dan isya untuk tempat Master, dihitung di
  PC ini tanpa internet dengan metode Kemenag RI (Subuh -20°, Isya -18°, Ashar Syafi'i,
  ihtiyath 2 menit yang bisa diatur), dalam WIB. Dia hanya menyebut waktunya, tanpa menasihati.
- Tempatnya `prayer.latitude`/`longitude`, atau koordinat atau kota di bagian `briefing` (kota
  dicari sekali lewat Open-Meteo, seperti untuk cuaca). Nama tempat yang dia sebut adalah
  `briefing.city`; koordinatnya tidak pernah dia sebut.
- Selama `waifu run` dia bilang sekali saat masuk waktu tiap sholat di `remind` (bawaan kelimanya):
  "Sayang, sudah masuk waktu Dzuhur.", atau `minutes_before` menit sebelumnya. Seperti pengingat
  yang Master pasang sendiri, juga di tengah blok fokus atau saat main game. Tidak diulang hari itu,
  juga setelah `waifu run` dijalankan ulang (`data\prayer.json`); yang terlewat lebih dari 30 menit
  tidak diucapkan. Ke HP hanya dengan `push: true`.
- Tetap jalan di mode tamu. `waifu sholat [YYYY-MM-DD]` menampilkan jadwalnya; `waifu check` punya
  baris `Waktu sholat`. README: "Waktu sholat".

## v1.40: pengingat istirahat

- Mati sampai dinyalakan (`breaks.enabled: true`): setelah satu jam terus memakai keyboard atau
  mouse di PC, Cyrene bilang sekali dengan lembut "Sayang, sudah satu jam di depan layar.
  Istirahatkan matamu sebentar dan minum air, ya.", lalu "sudah dua jam" sejam kemudian, dan
  seterusnya (`every_minutes`, 20 sampai 180 menit). Dia menunggu obrolan selesai dulu, dan tidak
  bicara ke kursi kosong kalau Master sudah berdiri.
- Jeda tanpa keyboard dan mouse selama 5 menit (`idle_reset_minutes`), atau PC yang sleep,
  dihitung istirahat: hitungannya mulai lagi dari nol.
- Tidak pernah di tengah blok fokus (teman fokus punya istirahatnya sendiri). Selagi mode game
  menahan pengumuman, kalimatnya ikut ditahan dan dilewati kalau sudah basi, kecuali
  `during_games: true`: dia tetap mengucapkannya selagi main. Jam aktif bisa diatur
  (`active_from`/`active_until`, boleh melewati tengah malam); ke HP hanya dengan `push: true`.
- Tidak ada yang disimpan: yang dibaca hanya berapa lama sejak keyboard atau mouse terakhir
  disentuh. Main dengan stik (controller) tidak terbaca sebagai aktif. `waifu check` punya baris
  `Pengingat istirahat`. README: "Pengingat istirahat".

## v1.39: belajar bahasa jepang

- Mati sampai dinyalakan (`japanese.enabled: true`): Cyrene mengajari satu kata Jepang tingkat
  pemula (JLPT N5) setiap hari dari daftar bawaan 300 kata dan ungkapan, tanpa internet. Kata hari
  ini dia sebut di akhir sapaan pagi (kana lalu artinya), hanya di sapaan itu (pertanyaan cuaca
  atau jadwal di jam lain tidak ikut menyebutnya), dan "kata Jepang hari ini apa?" bisa kapan
  saja, dengan contoh kalimatnya. Semua kata keluar dulu sebelum ada yang berulang.
- "Kuis bahasa Jepang dong": dia menanyakan arti satu kata yang sudah dipelajari, menunggu jawaban
  Master, lalu memuji atau menyebut artinya dengan lembut. Arti kata lain yang bunyinya sama
  (はな: bunga atau hidung) juga dihitung benar, dan jawaban untuk kata yang sama tidak dinilai
  dua kali sebelum waktunya diulang. Kata yang sering benar makin jarang ditanya (sistem
  Leitner), yang salah ditanya lagi besok. Kuis hanya kalau Master minta, tidak pernah di sapaan
  pagi. "Aku sudah belajar berapa kata?" menjawab kemajuannya.
- Bahasa Jepangnya selalu kana atau kanji, jadi diucapkan dengan suara Jepang; tanpa romaji.
- Tetap jalan di mode tamu. Kemajuan disimpan di `data\japanese.json` (hanya kode kata, kotak,
  dan tanggal). `waifu jepang` menampilkan kata hari ini dan `waifu jepang progres` kemajuannya,
  tanpa mencatat apa pun; `waifu check` punya baris `Belajar bahasa Jepang`. README: "Belajar
  bahasa Jepang".

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
