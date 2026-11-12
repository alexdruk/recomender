# Library inventory

Built from `data/books.db` by `src/inventory.js`. All archive books carry the label `liked`.

## Totals

| What | Count |
| --- | --- |
| Archive.zip size | 336.2 MB |
| Files unpacked (without macOS junk) | 22,047 |
| macOS junk files ignored (`__MACOSX`, `._*`) | 3 |
| Nested archives unpacked | 0 |
| Book files found | 311 |
| Parsed successfully | 311 (100.0%) |
| ... of which needed the lenient parser | 0 |
| Parse failures | 0 |
| Duplicates removed | 2 |
| Rows in `books` | 309 |
| Unique parsed books | 309 |
| Total text | 243,425,511 characters, 37,103,323 words |

## Files in the archive by extension

Most files are the insides of EPUB books (chapters, pictures, fonts).

| Extension | Files | Size MB |
| --- | --- | --- |
| .xhtml | 17,491 | 457.1 |
| .jpg | 1,380 | 140.9 |
| .otf | 1,051 | 49.8 |
| .png | 530 | 14.1 |
| (none) | 328 | 0.8 |
| .xml | 311 | 0.1 |
| .opf | 311 | 2.7 |
| .ncx | 311 | 2.1 |
| .css | 311 | 0.5 |
| .jpeg | 14 | 0.8 |
| .jfif | 5 | 1.5 |
| .webp | 3 | 0.3 |
| .jpg_0 | 1 | 0.0 |

## Parse failures

None. Every book file in the archive was parsed.

## Duplicates

Two books are duplicates when author + title are the same after lowercasing, replacing ё with е and removing punctuation (middle names are ignored). The longest text is kept.

| Dropped file | Title | Chars | Kept key | Kept chars |
| --- | --- | --- | --- | --- |
| Efiminyuk_S-privetom-iz-drugogo-mira-.801990.fb2.epub | С приветом из другого мира! | 591,290 | 862715 | 591,369 |
| Dybovskaya_Kopiya-neverna.851238.fb2.epub | Копия неверна | 480,771 | 851238 | 480,771 |

## By format

| Format | Books |
| --- | --- |
| epub | 309 |

## By language

| Language | Books | Share |
| --- | --- | --- |
| ru | 308 | 99.7% |
| (not stated) | 1 | 0.3% |

## Original vs translated

A book counts as translated when it names a translator, or when its source language differs from its language.

| Kind | Books | Share |
| --- | --- | --- |
| Original | 255 | 82.5% |
| Translated | 54 | 17.5% |

No book in the archive states its source language (the EPUB files do not carry that field).

## By genre

Books with at least one genre: 0 of 309 (0.0%).

**No genre data.** The archive books are EPUB files converted from FB2, and the converter did not copy the genre into the EPUB metadata. Genres will have to come from somewhere else (for example the Flibusta book id, which is in every file name).

## Top 30 authors

216 different authors in total.

| # | Author | Books | Words |
| --- | --- | --- | --- |
| 1 | Карина Демина | 11 | 924,658 |
| 2 | Анюта Соколова | 8 | 386,569 |
| 3 | Милена Валерьевна Завойчинская | 7 | 903,002 |
| 4 | Сергей Мусаниф | 7 | 515,648 |
| 5 | Татьяна Солодкова | 6 | 762,451 |
| 6 | Юлия Журавлева | 5 | 481,783 |
| 7 | Павел Иевлев | 5 | 275,728 |
| 8 | Ольга Александровна Куно | 4 | 440,052 |
| 9 | Валентина Елисеева | 4 | 408,005 |
| 10 | Тата Алатова | 4 | 298,019 |
| 11 | Рада Мэй | 4 | 276,757 |
| 12 | Марина Владимировна Ефиминюк | 4 | 256,649 |
| 13 | Сева Сотх | 4 | 250,691 |
| 14 | Андрей Александрович Васильев | 3 | 2,672,268 |
| 15 | Кирилл Клеванский | 3 | 522,953 |
| 16 | Алёна Харитонова | 3 | 324,339 |
| 17 | Екатерина Владимировна Казакова | 3 | 324,339 |
| 18 | Анна Шнайдер | 3 | 314,286 |
| 19 | Юлия Стешенко | 3 | 304,155 |
| 20 | Борис Конофальский | 3 | 297,757 |
| 21 | Роман Артемьев | 3 | 265,139 |
| 22 | Фредрик Бакман | 3 | 241,683 |
| 23 | Мария Морозова | 3 | 236,513 |
| 24 | Александра Лисина | 3 | 221,960 |
| 25 | Ольга Дмитриева | 3 | 211,127 |
| 26 | Хелен Харпер | 3 | 202,978 |
| 27 | Ольга Алексеевна Ярошинская | 3 | 199,208 |
| 28 | Ульяна Муратова | 3 | 181,211 |
| 29 | Александра Плен | 3 | 121,363 |
| 30 | Наталья Филимонова | 3 | 61,318 |

## Series

140 books (45.3%) belong to a series; 169 are standalone. There are 107 different series. Some of them are publisher series rather than story series.

Series with more than one book (24):

| Series | Books | Numbers present |
| --- | --- | --- |
| Ретро Бит | 4 | 1, 2, 5, 8 |
| Без огня | 3 | 1, 2, 3 |
| Матабар | 3 | 1, 2, 3 |
| Руководство по магии для ленивых | 3 | 1, 2, 3 |
| Струны волшебства | 3 | 1, 2, 3 |
| Тринадцатая дочь | 3 | 1, 2, 3 |
| Ходящие В Ночи | 3 | 1, 2, 3 |
| Целитель магических животных | 3 | 1, 2, 4 |
| Велейская империя | 2 | 2, 3 |
| Гостиница для магических животных | 2 | 1, 2 |
| Дочь Скорпиона | 2 | 3, 7 |
| Другие грабли | 2 | 1, 2 |
| Камелия. Княжна соляных пустошей | 2 | 1, 2 |
| Корморан Страйк | 2 | 7, 8 |
| Наемник | 2 | 1, 2 |
| Отель потерянных душ | 2 | 1, 2 |
| Перворожденный/Забракованные - общий мир | 2 | 1, 2 |
| Прогресс и магия | 2 | 2, 3 |
| Реонерия | 2 | 3, 4 |
| Сага о Видящих | 2 | 1, 2 |
| Седьмая мапа | 2 | 1, 2 |
| След паука | 2 | 1, 2 |
| Эльфийский бык | 2 | 2, 3 |
| Ювелир | 2 | 1, 2 |

Series with a single book: 83.

## Length distribution

Words per book: shortest 718, lower quarter 60,967, median 77,591, upper quarter 108,000, longest 2,387,278.

| Words | Books | Share |
| --- | --- | --- |
| under 10,000 (story) | 6 | 1.9% |
| 10,000 - 39,999 (novella) | 23 | 7.4% |
| 40,000 - 79,999 (short novel) | 135 | 43.7% |
| 80,000 - 119,999 (novel) | 82 | 26.5% |
| 120,000 - 199,999 (long novel) | 35 | 11.3% |
| 200,000 - 499,999 (very long or omnibus) | 21 | 6.8% |
| 500,000 and more (multi-book collection) | 7 | 2.3% |

Longest books:

| Key | Title | Words |
| --- | --- | --- |
| 837946 | "Лучшее из лучшего".Компиляция. Книги 1-30 | 2,387,278 |
| 604488 | Пиранья. Книги 1-23 | 1,752,022 |
| 586283 | Цикл «Файролл» [16 книг] | 1,685,473 |
| 767685 | А. Смолин, ведьмак | 900,964 |
| 604481 | Сказания Меекханского пограничья. Книги 1-5 | 810,047 |

## Decade distribution

The year is the date the book was written, as stated in the file. When that is missing, the publication year is used instead.

| Decade | Books | Share |
| --- | --- | --- |
| 1900s | 2 | 0.6% |
| 1950s | 1 | 0.3% |
| 1960s | 2 | 0.6% |
| 1970s | 3 | 1.0% |
| 1980s | 3 | 1.0% |
| 1990s | 9 | 2.9% |
| 2000s | 24 | 7.8% |
| 2010s | 63 | 20.4% |
| 2020s | 122 | 39.5% |
| (no year) | 80 | 25.9% |
