# Library inventory

Built from `data/books.db` by `src/inventory.js`. All archive books carry the label `liked`.

## Totals

| What | Count |
| --- | --- |
| Archive.zip size | 336.2 MB |
| Files unpacked (without macOS junk) | 22,047 |
| macOS junk files ignored (`__MACOSX`, `._*`) | 4 |
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

| Source language | Books |
| --- | --- |
| en | 38 |
| ru | 19 |
| sv | 5 |
| fr | 3 |
| pl | 2 |
| no | 1 |
| ja | 1 |
| it | 1 |
| es | 1 |
| de | 1 |

## By genre

Books with at least one genre: 309 of 309 (100.0%).

| Genre | Books |
| --- | --- |
| love_sf | 125 |
| network_literature | 122 |
| sf_fantasy | 43 |
| popadancy | 34 |
| sf_detective | 27 |
| sf_action | 23 |
| prose_contemporary | 22 |
| sf_fantasy_city | 22 |
| sf_humor | 22 |
| sf_social | 13 |
| sf_heroic | 10 |
| thriller | 9 |
| fan_translation | 8 |
| sf_history | 8 |
| sf | 7 |
| sf_space | 7 |
| sf_technofantasy | 7 |
| adventure | 6 |
| detective | 6 |
| love_contemporary | 6 |
| adv_history | 5 |
| boyar_anime | 5 |
| russian_fantasy | 5 |
| dark_fantasy | 4 |
| det_police | 4 |
| everyday_fantasy | 4 |
| love_erotica | 4 |
| sf_cyberpunk | 4 |
| sf_litrpg | 4 |
| sf_realrpg | 4 |
| sf_stimpank | 4 |
| det_crime | 3 |
| det_hard | 3 |
| det_history | 3 |
| fantasy_det | 3 |
| magic_school | 3 |
| nonf_biography | 3 |
| prose_history | 3 |
| prose_military | 3 |
| adv_maritime | 2 |
| prose_magic | 2 |
| sf_mystic | 2 |
| sf_postapocalyptic | 2 |
| sf_su | 2 |
| adv_geo | 1 |
| adv_indian | 1 |
| adv_modern | 1 |
| adventure_fantasy | 1 |
| det_action | 1 |
| det_classic | 1 |
| det_irony | 1 |
| dorama | 1 |
| dystopian | 1 |
| fairy_fantasy | 1 |
| foreign_prose | 1 |
| humor_prose | 1 |
| love | 1 |
| love_history | 1 |
| love_short | 1 |
| nonf_publicism | 1 |
| other | 1 |
| prose_counter | 1 |
| prose_rus_classic | 1 |
| prose_su_classics | 1 |
| sci_culture | 1 |
| sci_history | 1 |
| sf_epic | 1 |
| sf_etc | 1 |
| sf_horror | 1 |
| slavic_fantasy | 1 |
| story | 1 |
| unfinished | 1 |

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

219 books (70.9%) belong to a series; 90 are standalone. There are 169 different series. Some of them are publisher series rather than story series.

Series with more than one book (35):

| Series | Books | Numbers present |
| --- | --- | --- |
| Реонерия | 4 | 1, 3, 3, 4 |
| Ретро Бит | 4 | 1, 2, 5, 8 |
| Альганна | 3 | 1, 7, 7 |
| Ассоциация жильцов | 3 | 1, 2, 3 |
| Без огня | 3 | 1, 2, 3 |
| В полушаге от любви | 3 | 1, 2, 3 |
| Велейская империя | 3 | 1, 2, 3 |
| Матабар | 3 | 1, 2, 3 |
| Руководство по магии для ленивых | 3 | 1, 2, 3 |
| Струны волшебства | 3 | 1, 2, 3 |
| Тринадцатая дочь | 3 | 1, 2, 3 |
| Ходящие В Ночи | 3 | 1, 2, 3 |
| Целитель магических животных | 3 | 1, 2, 4 |
| Ведьмы и море | 2 | 1, 2 |
| Гостиница для магических животных | 2 | 1, 2 |
| Дочь Скорпиона | 2 | 3, 7 |
| Другие грабли | 2 | 1, 2 |
| Камелия. Княжна соляных пустошей | 2 | 1, 2 |
| Корморан Страйк | 2 | 7, 8 |
| Кофейные истории | 2 | 16 |
| Логры | 2 | 1, 2 |
| Наемник | 2 | 1, 2 |
| Некроманты и все-все-все | 2 | 1, 2 |
| Отель потерянных душ | 2 | 1, 2 |
| Перворожденный/Забракованные - общий мир | 2 | 1, 2 |
| Прогресс и магия | 2 | 2, 3 |
| Сага о Видящих | 2 | 1, 2 |
| Сайгорос | 2 | 1, 2 |
| Седьмая мапа | 2 | 1, 2 |
| Система дефрагментации | 2 | 7, 9 |
| След паука | 2 | 1, 2 |
| Три мира Ксении Белкиной | 2 | 1, 2 |
| Целители магических животных | 2 | 3, 5 |
| Эльфийский бык | 2 | 2, 3 |
| Ювелир | 2 | 1, 2 |

Series with a single book: 134.

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
| 100s | 1 | 0.3% |
| 1900s | 2 | 0.6% |
| 1950s | 1 | 0.3% |
| 1960s | 3 | 1.0% |
| 1970s | 3 | 1.0% |
| 1980s | 3 | 1.0% |
| 1990s | 10 | 3.2% |
| 2000s | 26 | 8.4% |
| 2010s | 63 | 20.4% |
| 2020s | 167 | 54.0% |
| (no year) | 30 | 9.7% |
