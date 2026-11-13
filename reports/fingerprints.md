# Text fingerprints and ratings

Built by `model/fingerprints.py` (run everything with `npm run fingerprints`). Milestone 3 asked whether the *text itself* helps to find my books: how a book is written (stylometry) and what it is about (embeddings). Milestone 3b adds the reader ratings from Flibusta, which is how I really choose books, and a harder comparison set: books that are rated as well as my liked books.

## The three plain answers

1. **What the ratings table contains.** One row per reader per book: 2,516,360 votes from 93,283 readers on 365,663 books. Each row has a reader number, a book number and a mark from 1 to 5. **Votes have no dates**, so nothing can be said about how fast a new book collects votes.

2. **Does text beat model rb on the rated-matched set?** **No.** rb scores 0.723 ± 0.042, rb+text scores 0.719 ± 0.033. Measured fold by fold on the same folds the difference is -0.004 ± 0.031 (better in 21 of 50 folds). That is not more than the fold-to-fold noise of the difference, so text is not shown to add anything beyond ratings and genres.

3. **Does the like-minded readers score beat both?** **Yes, when it is added to them; no on its own.** Adding the score to rb changes the AUC by +0.020 ± 0.016 (better in 46 of 50 folds); adding it to rb+text by +0.020 ± 0.015 (better in 47 of 50 folds). The best model of all is **lm+rb**: 0.743 ± 0.041 (+0.020 ± 0.016 against rb, +0.024 ± 0.032 against rb+text). The score alone (model lm, three numbers and nothing else) reaches 0.713, which is level with rb and rb+text, not above them (against rb -0.010 ± 0.035, against rb+text -0.006 ± 0.045). There are 796 like-minded readers when all liked books are used, and 646 on average inside a training part.

"More than the noise" means everywhere in this report: the average gain over the 50 test folds is bigger than the spread of that gain from fold to fold.

## Books used

| Group | Books |
| --- | --- |
| Liked books in the archive | 309 |
| ... with FB2 text downloaded from the mirror | 309 (100.0%) |
| ... still on EPUB text (FB2 could not be downloaded) | 0 |
| ... left out as collections | 11 |
| ... left out as non-fiction | 4 |
| **Liked books used (positive class)** | **294** |
| Rated-matched set: books picked from the catalog | 1046 |
| ... reused from the old matched set | 95 |
| ... new: downloaded and parsed | 951 |
| ... new: download or parse failed | 0 |
| ... new: not tried yet | 0 |
| **Rated-matched books used (source = rated-matched)** | **1044** |
| Old matched set: books picked from the catalog | 1150 |
| ... downloaded and parsed | 1150 |
| ... download or parse failed | 0 |
| **Old matched books used** | **1150** (95 of them are also in the rated-matched set) |
| **Feed books (only scored, never trained on)** | **314** |
| Different author groups (co-authors count as one group) | 1855 |

**The two comparison sets.** Both are Russian-language fiction from the catalog: not liked, not deleted, not a collection, at least 20,000 words when the catalog knows the size.

- **Rated-matched** (the one that counts): every book has at least 5 votes, and the books were drawn so that their years *and* their average ratings (in steps of a quarter star) follow the liked books that have at least 5 votes. A model cannot win here just by saying "this book is well rated".
- **Old matched** (easier): a random sample matched on year only. Most of these books have few votes or a low average, so the rating alone already separates them from my books.

A parsed book is dropped when it turns out to be a collection, a duplicate, or not Russian-language. Genre was not matched in either set.

| Pile | Books | Year: lower quarter / median / upper quarter | Average rating: lower quarter / median / upper quarter | Votes: median |
| --- | --- | --- | --- | --- |
| Liked | 294 | 2016 / 2022 / 2024 | 3.73 / 4.00 / 4.22 | 28 |
| Liked with at least 5 votes | 274 | 2016 / 2022 / 2024 | 3.73 / 4.00 / 4.21 | 30 |
| Rated-matched | 1044 | 2016 / 2022 / 2024 | 3.69 / 3.94 / 4.20 | 12 |
| Old matched | 1150 | 2016 / 2022 / 2024 | 1.71 / 2.69 / 3.57 | 2 |
| Feed | 314 | 2026 / 2026 / 2026 | 1.00 / 1.50 / 2.33 | 0 |

Average ratings are over books that have at least one vote. Years and average ratings of the rated-matched set follow the liked books; the number of votes was not matched.

### Download failures

Every liked book has FB2 text.

Rated-matched books: no failures in 951 downloads.

Old matched books: no failures in 1150 downloads.

## Ratings in the catalog

### What the ratings table holds

The table `librate` has the columns `ID`, `BookId`, `UserId`, `Rate`. It is **one row per reader per book**, not a total per book: 2,516,360 rows, 93,283 different readers, 365,663 different books. No reader has two rows for the same book. The readers are only numbers, with no names. **There is no date or time column**, so the table does not say when a vote was given.

| Mark | Votes | Share |
| --- | --- | --- |
| 1 | 569,152 | 22.6% |
| 2 | 240,618 | 9.6% |
| 3 | 329,495 | 13.1% |
| 4 | 523,386 | 20.8% |
| 5 | 853,709 | 33.9% |

Because every reader's votes can be followed from book to book, the "like-minded readers" score further down is possible.

### My rule against the data

My rule: ignore books with an average below 3, prefer books above 4, trust an average with at least 5 votes, and rarely read books with no ratings. The tables compare my liked books with all Russian-language fiction in the catalog (not deleted).

Share of books by average rating, counting every book that has at least one vote:

| Pile | Books | Average above 4 | Between 3 and 4 | Below 3 | No votes |
| --- | --- | --- | --- | --- | --- |
| Liked | 294 | 46.3% | 51.7% | 1.7% | 0.3% |
| All Russian-language fiction in the catalog | 425639 | 12.7% | 18.6% | 23.8% | 45.0% |
| Rated-matched set | 1044 | 39.8% | 58.2% | 1.9% | 0.0% |
| Old matched set | 1150 | 8.7% | 23.6% | 38.2% | 29.6% |
| Feed (a few days old) | 314 | 1.3% | 5.4% | 26.1% | 67.2% |

The same, counting only averages that can be trusted (at least 5 votes):

| Pile | Books | At least 5 votes | 1 to 4 votes | No votes | Among books with at least 5 votes: above 4 / 3 to 4 / below 3 |
| --- | --- | --- | --- | --- | --- |
| Liked | 294 | 93.2% | 6.5% | 0.3% | 46.7% / 51.5% / 1.8% |
| All Russian-language fiction in the catalog | 425639 | 18.5% | 36.5% | 45.0% | 19.9% / 37.9% / 42.1% |
| Rated-matched set | 1044 | 100.0% | 0.0% | 0.0% | 39.8% / 58.2% / 1.9% |
| Old matched set | 1150 | 29.9% | 40.5% | 29.6% | 7.8% / 36.6% / 55.5% |
| Feed (a few days old) | 314 | 4.1% | 28.7% | 67.2% | 0.0% / 46.2% / 53.8% |

How the numbers are spread (the 10% / 25% / 50% / 75% / 90% points). Example: a 25% point of 3.6 means a quarter of the books are below 3.6.

| Pile | Average rating (books with a vote) | Number of votes (all books) |
| --- | --- | --- |
| Liked | 3.47 / 3.73 / 4.00 / 4.22 / 4.45 | 6 / 13 / 28 / 54 / 99 |
| All Russian-language fiction in the catalog | 1.00 / 2.00 / 3.00 / 4.00 / 5.00 | 0 / 0 / 1 / 3 / 9 |

**What this says about the rule.** 98.0% of my liked books have an average of 3 or more, and only 1.7% are below 3 (0.3% have no votes). In the whole catalog only 31.3% of fiction books have an average of 3 or more, and 45.0% have no votes at all. 93.2% of my books have at least 5 votes, against 18.5% in the catalog. So the data agrees with the rule: almost everything I read is rated, and rated 3 or higher. "Above 4" is a preference, not a wall: 46.3% of my books are above 4 and 51.7% sit between 3 and 4.

### How fast a new book collects votes

**This cannot be answered from the catalog.** The question was how many days after upload a book usually reaches 5 and 10 votes. Votes carry no date, so the dump only shows how many votes a book has today. The only hint is the feed: 314 books that are a few days old, of which 4.1% already have at least 5 votes and 67.2% have none.

## Honest checks on the piles

### Check 1: can the text tell a feed book from an old matched book?

A model was trained to separate the 314 feed books from the 1150 old matched books, using only the embeddings (author-grouped, 5 folds, 10 repeats). **ROC AUC 0.713 ± 0.034.** 0.5 would mean the two piles look the same.

This is not above 0.75, so the upload week is not strongly visible in the text.

### Check 2: does the file format change the embedding?

For 30 liked books the EPUB text and the FB2 text of the same book were embedded separately. **Average cosine similarity 1.0000** (lowest 0.9998). For comparison, two *different* liked books have 0.843 on average. 1.0 means identical.

So the format barely moves a book's vector compared with the distance between books.

## How well the models rank

Positive = liked books. Each model is trained and tested twice: once against the rated-matched set and once against the old matched set. No model uses the year. All are logistic regressions:

- **r** - average rating + number of votes
- **rb** - r + genres, length, translated, series
- **rb+text** - rb + stylometry + embeddings
- **lm** - like-minded readers score alone
- **lm+rb** - like-minded readers + rb
- **lm+rb+text** - like-minded readers + rb+text
- **b0** - genres + length, translated, series (no ratings)
- **s** - b0 + stylometry
- **e** - embeddings only
- **be** - b0 + embeddings
- **all** - b0 + stylometry + embeddings

Embeddings: 50 PCA directions, learned inside each training fold. Test: author-grouped, 5 folds, 10 repeats (50 test folds); an author is never on both sides. Every scale, the model's penalty strength and the list of like-minded readers are also found inside the training fold only.

| Model | **Rated-matched: ROC AUC** | Rated-matched: median percentile of a liked book | Rated-matched: liked books in the top 5% | Old matched: ROC AUC | Old matched: liked books in the top 5% | AUC liked vs "would never read" |
| --- | --- | --- | --- | --- | --- | --- |
| r: average rating + number of votes | **0.671 ± 0.053** | 24.0% ± 6.4 | 15.1% ± 6.2 | 0.958 ± 0.013 | 76.6% ± 7.3 | 0.702 ± 0.006 |
| rb: r + genres, length, translated, series | **0.723 ± 0.042** | 17.7% ± 4.7 | 17.5% ± 8.1 | 0.962 ± 0.012 | 79.8% ± 7.3 | 0.746 ± 0.008 |
| rb+text: rb + stylometry + embeddings | **0.719 ± 0.033** | 20.6% ± 5.3 | 16.2% ± 6.2 | 0.959 ± 0.012 | 77.5% ± 8.2 | 0.750 ± 0.011 |
| lm: like-minded readers score alone | **0.713 ± 0.047** | 19.0% ± 6.9 | 21.7% ± 8.9 | 0.924 ± 0.022 | 66.1% ± 10.3 | 0.733 ± 0.007 |
| lm+rb: like-minded readers + rb | **0.743 ± 0.041** | 15.6% ± 5.5 | 22.9% ± 10.3 | 0.964 ± 0.011 | 80.1% ± 7.3 | 0.765 ± 0.009 |
| lm+rb+text: like-minded readers + rb+text | **0.739 ± 0.034** | 18.1% ± 5.5 | 21.4% ± 7.4 | 0.964 ± 0.011 | 82.0% ± 6.9 | 0.767 ± 0.010 |
| b0: genres + length, translated, series (no ratings) | **0.660 ± 0.049** | 27.1% ± 8.0 | 15.4% ± 6.5 | 0.723 ± 0.042 | 21.7% ± 8.3 | 0.668 ± 0.014 |
| s: b0 + stylometry | **0.664 ± 0.049** | 27.7% ± 7.4 | 13.0% ± 6.0 | 0.768 ± 0.039 | 26.0% ± 8.5 | 0.679 ± 0.008 |
| e: embeddings only | **0.659 ± 0.037** | 28.0% ± 5.7 | 13.1% ± 4.8 | 0.778 ± 0.035 | 30.0% ± 6.9 | 0.681 ± 0.010 |
| be: b0 + embeddings | **0.675 ± 0.039** | 26.6% ± 4.7 | 12.9% ± 5.4 | 0.791 ± 0.034 | 31.1% ± 7.5 | 0.701 ± 0.010 |
| all: b0 + stylometry + embeddings | **0.679 ± 0.037** | 25.3% ± 5.0 | 12.4% ± 5.2 | 0.802 ± 0.033 | 33.9% ± 9.9 | 0.714 ± 0.011 |
| all+labels: all, with my answers | **0.680 ± 0.036** | 25.5% ± 4.7 | 12.1% ± 4.9 | 0.802 ± 0.033 | 34.0% ± 9.7 | 0.719 ± 0.010 |

How to read the numbers:

- **ROC AUC**: 0.5 is coin-flipping, 1.0 is a perfect ranking. The number after ± is how much it moves from fold to fold.
- **Median percentile**: each liked test book is placed in the ranking of the comparison test books. 10% means the typical liked book sits in the top tenth of the pile; 50% would be pure chance. Lower is better.
- **Top 5%**: the share of liked test books that score higher than 95% of the comparison test books. Chance gives 5%.
- **The rated-matched columns are the ones that count.** On the old matched set the rating does most of the work, because that set is full of books with few votes or low averages. On the rated-matched set the average rating was matched away on purpose, so model r can only use the number of votes.

- **AUC liked vs "would never read"**: how well the score (trained against the rated-matched set) separates liked books from the books I marked "would never read" (scores from the author-grouped test; ± is over the repeats). This is the cleanest number, because both sides are real answers.

### Does one model really beat another?

Each row compares two models fold by fold on the same 50 folds, which removes most of the fold-to-fold noise.

| Comparison | What it tests | Rated-matched: AUC gain | Real gain on rated-matched? | Old matched: AUC gain |
| --- | --- | --- | --- | --- |
| rb against r | genres, length, translated, series on top of ratings | +0.052 ± 0.039 (better in 45 of 50 folds) | yes, more than the noise | +0.004 ± 0.004 (better in 44 of 50 folds) |
| rb+text against rb | **text on top of ratings and genres** | -0.004 ± 0.031 (better in 21 of 50 folds) | no, inside the noise | -0.002 ± 0.007 (better in 16 of 50 folds) |
| lm against rb | like-minded readers alone, against rb | -0.010 ± 0.035 (better in 20 of 50 folds) | no, inside the noise | -0.037 ± 0.016 (better in 0 of 50 folds) |
| lm against rb+text | like-minded readers alone, against rb+text | -0.006 ± 0.045 (better in 27 of 50 folds) | no, inside the noise | -0.035 ± 0.017 (better in 1 of 50 folds) |
| lm+rb against rb | like-minded readers on top of rb | +0.020 ± 0.016 (better in 46 of 50 folds) | yes, more than the noise | +0.002 ± 0.004 (better in 38 of 50 folds) |
| lm+rb+text against rb+text | like-minded readers on top of rb+text | +0.020 ± 0.015 (better in 47 of 50 folds) | yes, more than the noise | +0.005 ± 0.005 (better in 42 of 50 folds) |
| lm+rb+text against lm+rb | text on top of like-minded readers + rb | -0.004 ± 0.026 (better in 22 of 50 folds) | no, inside the noise | -0.000 ± 0.005 (better in 26 of 50 folds) |
| all against b0 | text on top of genres, with no ratings at all | +0.019 ± 0.038 (better in 34 of 50 folds) | no, inside the noise | +0.079 ± 0.037 (better in 48 of 50 folds) |
| all+labels against all | my answers on top of model all | +0.001 ± 0.009 (better in 25 of 50 folds) | no, inside the noise | -0.000 ± 0.001 (better in 20 of 50 folds) |

### Does text add anything beyond ratings and genres?

**No.** On the rated-matched set rb scores 0.723 and rb+text scores 0.719. The gain is -0.004 and its fold-to-fold noise is ±0.031; rb+text is better in 21 of 50 folds. The gain is not bigger than its noise, so there is no proof that text adds anything once ratings, genres and length are known.

### The like-minded readers score

A like-minded reader is a reader who gave 4 or 5 to at least 3 of my liked books. Using all 294 liked books there are **796** such readers. In the test they are found from the liked books of the training part only: 646 on average (from 582 to 705).

For every book the score is three numbers: how many like-minded readers voted for it, what share of all its voters they are, and how much higher (or lower) they rated it than all readers did (a few votes are pulled towards the book's overall average, with the weight of 3 votes). A training book never helps to choose its own like-minded readers, and neither do other books by the same author; otherwise the score would look far better in training than it can be on a new author.

**Does it beat both rb and rb+text? Yes, when it is added to them; no on its own.** Best model with the score: lm+rb, 0.743 ± 0.041 on the rated-matched set (+0.020 ± 0.016 against rb, +0.024 ± 0.032 against rb+text). Added to rb it changes the AUC by +0.020 ± 0.016 (better in 46 of 50 folds); added to rb+text by +0.020 ± 0.015 (better in 47 of 50 folds).

### New uploads: liked books against feed books

Feed books are never trained on. Here each model's score is tested on "liked test books against feed test books".

| Model | Trained against | ROC AUC on liked vs feed | Median percentile | Top 5% |
| --- | --- | --- | --- | --- |
| all | rated-matched | 0.676 ± 0.052 | 26.6% ± 7.0 | 11.6% ± 8.7 |
| rb+text | rated-matched | 0.915 ± 0.034 | 2.9% ± 3.1 | 63.4% ± 15.0 |
| all | old matched | 0.771 ± 0.052 | 15.6% ± 7.5 | 25.5% ± 14.0 |
| rb+text | old matched | 0.992 ± 0.005 | 0.0% ± 0.0 | 96.3% ± 2.5 |

Model all uses no ratings, so its rows show how well taste in text and genre carries over to this week's uploads. The rb+text rows are a warning rather than a result: feed books have almost no votes yet, so against the old matched set a ratings model separates them for the wrong reason, and against the rated-matched set it has never seen a book with so few votes.

## My answers from the labelling page

Answers so far: **100** (58 would never read, 37 maybe, 5 want to read). Starred favourites: 0.

The page now shows the average rating and the number of votes on each card. Books with an average of 3 or more and at least 5 votes come first, and among those the rated-matched books come before the others.

There are at least 100 answers, so they are used: the extra column "liked vs would never read" for every model in the table above, and the extra model **all+labels** (model all, where "would never read" books count 3 times as negatives, starred books count 2 times as positives, and "want to read" books are not used as negatives). Books I already answered are left out of the two lists below.

### Genre groups: "would never read" against liked

| Genre group | Never: books | Never: share | Liked: books | Liked: share | Difference (points) |
| --- | --- | --- | --- | --- | --- |
| contemporary romance | 8 | 13.8% | 8 | 2.7% | +11.1 |
| popadantsy | 12 | 20.7% | 34 | 11.6% | +9.1 |
| contemporary prose | 8 | 13.8% | 21 | 7.1% | +6.7 |
| web literature | 26 | 44.8% | 118 | 40.1% | +4.7 |
| historical | 3 | 5.2% | 8 | 2.7% | +2.5 |
| adventure | 3 | 5.2% | 8 | 2.7% | +2.5 |
| horror and mystic | 2 | 3.4% | 3 | 1.0% | +2.4 |
| erotica | 2 | 3.4% | 4 | 1.4% | +2.1 |
| children | 1 | 1.7% | 0 | 0.0% | +1.7 |
| thriller | 2 | 3.4% | 8 | 2.7% | +0.7 |
| war prose | 1 | 1.7% | 3 | 1.0% | +0.7 |
| detective | 8 | 13.8% | 40 | 13.6% | +0.2 |
| litrpg | 1 | 1.7% | 5 | 1.7% | +0.0 |
| boyar-anime | 1 | 1.7% | 5 | 1.7% | +0.0 |
| other | 0 | 0.0% | 1 | 0.3% | -0.3 |
| classic prose | 0 | 0.0% | 2 | 0.7% | -0.7 |
| alternative history | 1 | 1.7% | 8 | 2.7% | -1.0 |
| fan translation | 1 | 1.7% | 8 | 2.7% | -1.0 |
| humour | 3 | 5.2% | 23 | 7.8% | -2.7 |
| science fiction | 7 | 12.1% | 46 | 15.6% | -3.6 |
| fantasy | 12 | 20.7% | 79 | 26.9% | -6.2 |
| romantic fantasy | 9 | 15.5% | 124 | 42.2% | -26.7 |

## Taste map: clusters of the liked books

The liked books were grouped by their embeddings (k-means on the book vectors, after taking away what all liked books have in common). The number of clusters was chosen by the silhouette score (higher = cleaner groups; values near 0 mean the groups overlap a lot).

| Clusters | Silhouette | Cluster sizes |  |
| --- | --- | --- | --- |
| 4 | 0.093 | 103, 73, 61, 57 | **chosen** |
| 5 | 0.086 | 82, 65, 59, 53, 35 |  |
| 6 | 0.080 | 67, 54, 50, 45, 44, 34 |  |
| 7 | 0.076 | 50, 48, 47, 42, 41, 34, 32 |  |
| 8 | 0.077 | 44, 43, 43, 38, 37, 33, 31, 25 |  |
| 9 | 0.067 | 44, 43, 38, 33, 30, 29, 28, 26, 23 |  |
| 10 | 0.067 | 44, 39, 34, 31, 31, 29, 27, 21, 21, 17 |  |

### Cluster 1: 103 books

- Most common authors: Анюта Соколова (8), Милена Валерьевна Завойчинская (5), Марина Владимировна Ефиминюк (4), Юлия Журавлева (4), Рада Мэй (4)
- Most common genre groups: romantic fantasy (87.4%), web literature (53.4%), fantasy (17.5%), detective (16.5%), humour (11.7%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Мария Морозова | Связанные целью |
| Ольга Куно | Шпионка в графском замке |
| Александра Лисина | Ведьма, тень и черный кот |
| Юлия Журавлева | Кафедра зооцелительства |
| Ольга Дмитриева | Обрученная с врагом (СИ) |
| Рада Мэй | Душа по обмену |
| Ольга Александровна Куно | В полушаге от любви |
| Милена Валерьевна Завойчинская | Струны волшебства. Книга вторая. Цветная музыка сидхе |

### Cluster 2: 73 books

- Most common authors: Карина Демина (3), Екатерина Владимировна Казакова (3), Кирилл Клеванский (3), Анджей Сапковский (2), Алёна Харитонова (2)
- Most common genre groups: fantasy (52.1%), science fiction (28.8%), web literature (27.4%), romantic fantasy (23.3%), popadantsy (11.0%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Алёна Харитонова, Екатерина Владимировна Казакова | Пленники раздора |
| Алёна Артёмовна Харитонова, Екатерина Владимировна Казакова | Наследники Скорби |
| Кристофер Бьюлман | Вор с черным языком |
| Анджей Сапковский | Последнее желание |
| Сергей Вацлавович Малицкий | Пагуба |
| Гарри Гаррисон | К западу от Эдема |
| Юрий Александрович Никитин | Трое из Леса. Трилогия |
| Алёна Харитонова, Екатерина Владимировна Казакова | Жнецы Страданий |

### Cluster 3: 61 books

- Most common authors: Павел Иевлев (4), Сева Сотх (4), Фредрик Бакман (3), Сергей Сергеевич Мусаниф (3), Сергей Мусаниф (2)
- Most common genre groups: web literature (26.2%), detective (26.2%), science fiction (23.0%), contemporary prose (21.3%), thriller (11.5%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Зиновий Юрьевич Юрьев | Финансист на четвереньках |
| Сева Сотх | Ретро Бит [8] |
| Сева Сотх | Ретро бит [5] |
| Сева Сотх | Ретро Бит [2] |
| Сева Сотх | Ретро Бит |
| Дин Кунц | Славный парень |
| Майк У. Крэйвен | Шоу марионеток |
| Роберт Гэлбрейт | Человек с клеймом |

### Cluster 4: 57 books

- Most common authors: Карина Демина (6), Анатолий Брусникин (2), Борис Конофальский (2), Сергей Мусаниф (2), Инди Видум (2)
- Most common genre groups: web literature (47.4%), fantasy (28.1%), romantic fantasy (19.3%), science fiction (15.8%), contemporary prose (12.3%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Елена Ивановна Михалкова | Колодец и бабочка |
| Карина Демина | Эльфийский бык |
| Карина Демина | Ведьмы.Ру |
| Андрей Александрович Васильев | Слеза Рода |
| Владимир Осипович Богомолов | Момент истины (В августе сорок четвертого...) |
| Борис Конофальский | Сталин против рептилоидов |
| Владимир Алексеевич Ильин | Напряжение |
| Гоблин MeXXanik, Каин | Чехов. Книга 8. Темный всадник [СИ] |

## The top 30, in two lists

Every score comes from the author-grouped test against the rated-matched set: a book is scored only by models that never saw the book or its author, averaged over the repeats. The score is between 0 and 1. Scores of the two lists come from different models, so do not compare them across lists.

### Rated: at least 5 votes and an average of 3 or more

Scored by the model with the best ROC AUC on the rated-matched set, **lm+rb**. 1100 unlabeled books qualify.

| Score | Rating | Votes | Key | Author | Title | Genres | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.91 | 3.56 | 66 | 554182 | Марина Владимировна Ефиминюк | Черная ведьма желает познакомиться | love_sf | rated-matched |
| 0.91 | 4.44 | 55 | 819499 | Ольга Ярошинская | На пересдачу, Кэсси Рок! Четвертая попытка | love_sf, magic_school | rated-matched |
| 0.90 | 4.25 | 64 | 741494 | Татьяна Владимировна Солодкова | Душа королевского замка | love_sf, network_literature | rated-matched |
| 0.89 | 4.12 | 118 | 546689 | Дана Арнаутова | Стальной подснежник | love_sf | rated-matched |
| 0.89 | 4.33 | 51 | 575529 | Кира Алиевна Измайлова | Старое платье королевы | love_sf | rated-matched |
| 0.89 | 3.66 | 86 | 612428 | Оксана Чекменёва | Целительница моей души | love_sf, network_literature, sf_fantasy_city, everyday_fantasy | rated-matched |
| 0.88 | 3.64 | 42 | 815015 | Ива Лебедева, Ирина Владимировна Смирнова | В моем счастье прошу винить амнезию | sf_humor, love_sf | rated-matched |
| 0.88 | 4.14 | 73 | 789434 | Яна Ясная | Секс и ничего личного | love_sf, love_erotica, network_literature | rated-matched |
| 0.88 | 3.87 | 62 | 687368 | Марианна Красовская | Дух воина | love_sf | rated-matched |
| 0.88 | 4.32 | 47 | 712101 | Маргарита Александровна Гришаева | Приворот на василиска | love_sf, love_short | rated-matched |
| 0.86 | 3.59 | 66 | 458017 | Дарья Андреевна Кузнецова | Кошачья гордость, волчья честь | love_sf | rated-matched |
| 0.86 | 3.58 | 103 | 602417 | Нинель Мягкова | Отборная бабушка | love_sf, humor, network_literature, everyday_fantasy, popadancy | rated-matched |
| 0.85 | 4.57 | 562 | 173033 | Ольга Громыко | Верховная Ведьма | sf_fantasy, sf_humor | rated-matched |
| 0.84 | 4.07 | 29 | 702822 | Ольга Ярошинская | Огня для мисс Уокер! | sf_detective, love_sf | rated-matched |
| 0.84 | 4.03 | 34 | 681627 | Екатерина Сергеевна Бакулина | Мой покойный муж | love_sf, network_literature | rated-matched |
| 0.84 | 4.40 | 208 | 302744 | Оксана Петровна Панкеева | О пользе проклятий | love_sf | rated-matched |
| 0.84 | 4.32 | 340 | 150905 | Ольга Громыко | Год Крысы. Видунья | sf_fantasy | rated-matched |
| 0.83 | 4.29 | 344 | 320453 | Виталий Валерьевич Зыков | Под знаменем пророчества | sf_fantasy | rated-matched |
| 0.83 | 4.03 | 59 | 619223 | Яна Лисканова | План по соблазнению герцога (СИ) | love_sf, sf_humor, network_literature | rated-matched |
| 0.82 | 3.66 | 149 | 620295 | Дмитрий Олегович Смекалин | Господин маг | sf_heroic, sf_technofantasy | rated-matched |
| 0.82 | 4.16 | 62 | 577074 | Марина Суржевская | Королевство Бездуш. Lastfata | love_sf | rated-matched |
| 0.82 | 3.95 | 58 | 564647 | Алиса Чернышова | Истинная пара для дракона, или Просто полетели домой (СИ) | love_sf, network_literature | rated-matched |
| 0.81 | 3.99 | 119 | 348068 | Елена Звездная | Дочь воина, или Кадеты не сдаются | love_sf | rated-matched |
| 0.80 | 4.10 | 60 | 458349 | Елена Звездная | Урок седьмой: Опасность кровного наследия | love_sf, magic_school | rated-matched |
| 0.80 | 4.00 | 53 | 435620 | Анна Сергеевна Гаврилова | Шустрое счастье, или Охота на маленького дракона | love_sf | rated-matched |
| 0.80 | 4.06 | 48 | 519828 | Александра Лисина | Беглянка | love_sf | rated-matched |
| 0.79 | 3.83 | 52 | 603576 | Кристина Леола | Честная сделка | sf_humor, love_sf, network_literature | rated-matched |
| 0.79 | 3.00 | 17 | 710765 | Терин Рем | Мужская вакансия, или Женщина на борту — к несчастью | love_sf | rated-matched |
| 0.79 | 3.99 | 72 | 648543 | Анна Орлова | Овсянка, мэм! | sf_detective, det_irony | rated-matched |
| 0.79 | 3.99 | 77 | 435253 | Наталья Сергеевна Жильцова, Азалия Еремеева | Академия магического права. Брюнетка в защите | love_sf | rated-matched |

### Early: fewer than 5 votes

These books have too few votes for the rating to mean much, so they are scored by the best model that uses no ratings at all, **all** (text and genres only). 1107 unlabeled books qualify. Books with at least 5 votes and an average below 3 are in neither list: my rule says to ignore them.

| Score | Rating | Votes | Key | Author | Title | Genres | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.89 |  | 0 | 895490 | Марьяша Ришелье | Моя мерзкая фиалка | love_sf, sf_humor | feed |
| 0.87 |  | 0 | 895518 | Ия Эмдин | Отвергнутая драконом | love_sf | feed |
| 0.84 |  | 0 | 834457 | Автор Неизвестен -- Мифы. Легенды. Эпос. Сказания | Беовульф | antique_myths | matched |
| 0.82 |  | 0 | 895358 | Ефимия Летова | Исцели меня | love_erotica, love_sf | feed |
| 0.82 | 1.00 | 2 | 895284 | Ника Верон | Черт и Снежинка: не пара? | popadancy, sf_fantasy | feed |
| 0.81 | 3.00 | 1 | 773985 | Лана Эл | Обручальные цепи, или Охота на дракона | love_sf, network_literature | matched |
| 0.81 |  | 0 | 895600 | Елена Кара | Сжечь ведьму! Или согреть её сердце! | love_sf, network_literature | feed |
| 0.80 |  | 0 | 895307 | Софья Валерьевна Ролдугина | Осень со вкусом кофе. Коллекция уютных историй | love_contemporary | feed |
| 0.80 | 1.00 | 3 | 895251 | Daniil Polkovnikov | Вам письмо. Лично в руки | great_story, fanfiction, popadancy, sf_fantasy | feed |
| 0.80 |  | 0 | 740749 | Валерия Дашкевич | Лунная Мелодия | love_sf, network_literature | matched |
| 0.78 |  | 0 | 895673 | Лина Грин | Тыквенная лавка госпожи Браун | love_sf, popadancy | feed |
| 0.77 | 3.67 | 3 | 895250 | Степан Полосов | Сказка Колобок в исполнении различных писателей | llm_art, sf_humor | feed |
| 0.76 | 1.00 | 1 | 568839 | Полина Александровна Матыцына | Роберт и Мэг из замка внезапных чудес | sf_fantasy_city, child_tale, children | matched |
| 0.76 | 2.00 | 1 | 806682 | Дороти Освальд | Изумрудная ведьма | sf_fantasy, sf_heroic, sf_fantasy_city | matched |
| 0.76 |  | 0 | 330919 | Марианна Грубер | В Замок | prose_contemporary | matched |
| 0.75 |  | 0 | 895290 | Марьяша Ришелье | Дениз: инструкция по выживанию для некромантки. Жена для Властелина Тьмы | sf_humor | feed |
| 0.75 | 2.00 | 1 | 766737 | Регина Хайруллова | Ведьмочка для художника, или Возвращение в Мир Мечты | love_sf, network_literature | matched |
| 0.74 |  | 0 | 895249 | Ника Верон | Ада. История-бонус № 1 | love_short, love_sf | feed |
| 0.74 | 4.00 | 1 | 536980 | Пол Кемп | Свидетель тени | sf_fantasy, fan_translation | matched |
| 0.74 |  | 0 | 895380 | Кристина Корр | Ленивая злодейка на отборе невест | sf_fantasy, love_sf, humor_prose | feed |
| 0.74 | 1.00 | 1 | 611940 | Екатерина Бунькова | Дом с белыми клибисами | love_sf, network_literature | matched |
| 0.74 | 2.00 | 2 | 895378 | Надежда Цыбанова | Ремонт, магия и прочие неприятности | love_sf, detective, humor_prose | feed |
| 0.74 |  | 0 | 895520 | Илиоса | Господин ищет не любовницу | popadancy, love_sf, love_erotica, network_literature | feed |
| 0.74 |  | 0 | 524247 | Генри Райдер Хаггард | Лейденская красавица | prose_history, adv_history | matched |
| 0.73 |  | 0 | 895604 | Мари Александер | Бариста для Тёмного Лорда | love_short, love_sf, popadancy | feed |
| 0.73 | 2.00 | 1 | 895256 | Мария Камардина | Тёмное сердце Белого озера. Книга 1 | network_literature, love_sf | feed |
| 0.73 |  | 0 | 895278 | Таня Волова | Ангел и огненная душа | love_short, love_sf | feed |
| 0.72 |  | 0 | 895397 | Руслан Иванцов | Страж Луны | sf_fantasy | feed |
| 0.72 | 3.00 | 3 | 757206 | Михаил Рагимов, Игорь Игоревич Николаев | Плохие люди | sf_social, network_literature | matched |
| 0.72 |  | 0 | 895651 | Александр Сорокин, Павел Шимуро | Кодекс Магических Зверей XI | sf_fantasy, popadancy, other | feed |

## The 20 liked books with the lowest score

Liked books that the best model (lm+rb, trained against the rated-matched set) would have missed.

| Score | Rating | Votes | Key | Author | Title | Genres |
| --- | --- | --- | --- | --- | --- | --- |
| 0.11 | 3.00 | 2 | 641061 | Кормак Маккарти | Пограничная трилогия: Кони, кони… За чертой. Содом и Гоморра, или Города окрестности сей | adv_indian, prose_contemporary |
| 0.12 |  | 0 | 867164 | Макс Далин | Костер и Саламандра. Книга 3 | sf_heroic |
| 0.12 | 5.00 | 1 | 854472 | Олег Михайлович Куваев | Правила бегства | prose_su_classics |
| 0.14 | 3.75 | 4 | 816811 | Анна Владимирова | Мой бывший - зверь | love_sf, love_erotica, network_literature |
| 0.15 | 3.00 | 3 | 847472 | Александра Лисина | 12. След мастера | popadancy, network_literature, sf_technofantasy, unfinished, boyar_anime |
| 0.15 | 4.00 | 13 | 378528 | Александр Владимирович Мазин | Варяжская правда: Варяг. Место для битвы. Князь | russian_fantasy, popadancy |
| 0.16 | 4.00 | 4 | 833511 | Гарри Гаррисон | К западу от Эдема | sf_action, sf_etc, sf |
| 0.17 | 3.75 | 8 | 654233 | Марина и Сергей Дяченко | Ведьмин век. Трилогия | sf_fantasy_city, sf_social |
| 0.17 | 4.00 | 3 | 667371 | Ольга Николаевна Ларионова | Лабиринт для троглодитов | sf, sf_su |
| 0.20 | 4.43 | 7 | 802226 | Ирина Шайлина | Лишняя женщина | love_contemporary, network_literature |
| 0.20 | 4.60 | 5 | 430078 | Кэтрин Куртц | Дерини. Трилогия [ Возрождение Дерини. Шахматы Дерини. Властитель Дерини] | sf_fantasy |
| 0.21 | 4.14 | 7 | 849511 | Аня Истомина | Как приручить полковника | love_contemporary |
| 0.22 | 4.25 | 16 | 859155 | Александр Рудазов | Крылья Паргорона | sf_epic, dark_fantasy, network_literature |
| 0.22 | 5.00 | 3 | 845780 | Алёна Харитонова, Екатерина Владимировна Казакова | Пленники раздора | sf_action, russian_fantasy |
| 0.22 | 3.75 | 8 | 685747 | Фред Варгас | Человек, рисующий синие круги | det_police, thriller |
| 0.24 | 4.50 | 6 | 682658 | Тори Ру | Время неба | love_contemporary |
| 0.24 | 2.68 | 31 | 847476 | Виктор Гросов | Ювелиръ. 1808 | sf_history, popadancy, network_literature |
| 0.24 | 4.40 | 5 | 771829 | Самюэль Бьорк | Хитра | thriller, det_police |
| 0.25 | 4.42 | 19 | 316768 | Патрик О'Брайан | Командир и штурман | adv_history, adv_maritime, prose_history |
| 0.25 | 4.20 | 5 | 806624 | Наталья Филимонова | Сорок дракошек | network_literature, everyday_fantasy, popadancy, sf_humor |

## Appendix: stylometry averages

The numbers measured on each book's full text, averaged per pile.

| Measure | Liked | Rated-matched | Old matched | Feed |
| --- | --- | --- | --- | --- |
| average sentence length (words) | 8.91 | 9.12 | 9.52 | 8.87 |
| spread of sentence length | 6.64 | 6.75 | 6.88 | 6.3 |
| share of very short sentences (3 words or fewer) | 0.221 | 0.215 | 0.198 | 0.219 |
| average paragraph length (words) | 39.3 | 26 | 30.3 | 26.2 |
| share of paragraphs that are dialogue lines (start with a dash) | 0.511 | 0.519 | 0.481 | 0.435 |
| share of first-person pronouns (я, меня, мой ...) | 0.0293 | 0.03 | 0.0284 | 0.0328 |
| share of second-person pronouns (ты, вы ...) | 0.0131 | 0.0131 | 0.0127 | 0.0123 |
| vocabulary richness: different words in a 5000-word sample | 0.465 | 0.46 | 0.455 | 0.443 |
| exclamation marks per 1,000 words | 7.61 | 8.09 | 8.4 | 6.26 |
| question marks per 1,000 words | 12.9 | 12.6 | 11.4 | 10.7 |
| ellipses per 1,000 words | 7.83 | 7.08 | 5.54 | 4.98 |
| commas per 1,000 words | 117 | 115 | 113 | 112 |
| average chapter length (log of words) | 3.56 | 3.58 | 3.53 | 3.37 |
| average word length (letters) | 5.23 | 5.23 | 5.22 | 5.19 |
| share of long words (10 letters or more) | 0.0925 | 0.0923 | 0.0913 | 0.087 |
| numbers per 1,000 words | 0.744 | 1.05 | 1.66 | 1.62 |
| Latin-alphabet words per 1,000 words | 0.492 | 0.514 | 0.721 | 0.953 |
