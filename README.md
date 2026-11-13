# Book recommender

A personal, fully local book recommender for the Flibusta daily updates.

## What it does

1. **Downloads** the daily update files (`f.fb2.*.zip`) from `https://flibusta.is/daily/`
   (fallback `https://flibusta.site/daily/`), one at a time, with a pause of at
   least 3 seconds between requests. Each zip is parsed and then deleted.
2. **Filters** the new books: Russian language, fiction, not a collection, at
   least 10,000 words, not already in my library, and the series rule
   (standalone, or book 1, or a series where I already liked an earlier book).
3. **Fingerprints** every book that passed, from its text only: stylometry,
   embeddings of the whole text and of the opening (first 25,000 words, model
   bge-m3), genre codes from the FB2 file, length, translated or not.
   Ratings, votes, comments and the year are never used.
4. **Scores** the book with a model trained on my liked books against a
   background of ordinary books, and stores the score and its percentile among
   all feed books scored so far.
5. **Shows** the best books on a local page with **Like** and **Dislike**
   buttons. The model retrains after every 10 presses (a Dislike counts 3
   times) and rescores the books that are still waiting.

## Commands

| Command | What it does |
| --- | --- |
| `npm start` | Starts the page at http://localhost:3000. Runs the daily loop at start and then every 6 hours. |
| `npm run daily` | Runs the daily loop once: download, parse, filter, fingerprint, score. |
| `npm run train` | Trains the model, saves it to `data/model/recommender.joblib`, prints the quality, rescores waiting books. |
| `npm run autostart-on` | Starts the server at every login and keeps it running (macOS launchd). Log: `data/server.log`. |
| `npm run autostart-off` | Stops the server and removes the autostart job. |

## The page

- **Recommended**: unanswered books in the top 5% (the share can be changed on
  the page), best first. Each card shows author, title, series, genres, length,
  annotation, score, the 3 liked books closest by text, and a link to the book.
- **Show the rest**: the other books of the last 7 days, ranked.
- **Status line**: last download, books analysed, books that passed the
  filters, answers given, model quality from the last training.
- **Check now**: runs the daily loop right away.
- A yellow warning appears if update files left the listing before they were
  downloaded (the computer was off for too long).

## Settings (`config.json`)

`filters` (each filter can be switched off or changed), `topPercent`,
`restDays`, `retrainEveryAnswers`, `checkEveryHours`, `port`, `mirrors`,
`pauseSeconds`.

## Where things are

- `src/daily.js` the daily loop, `src/fetch-feed.js` the polite downloader,
  `src/parse-fb2.js` the FB2 parser, `src/stylometry.js` the style numbers.
- `src/server.js` and `src/page.html` the page.
- `model/recommend.py` training and scoring, `model/embed.py` embeddings.
- `data/` (not in git): the database `books.db`, texts, vectors, the model, the log.

Needs Node 22 (`npm install`) and Python in `.venv`
(`.venv/bin/pip install -r model/requirements.txt`). The older scripts and the
reports in `reports/` are the research that led to this model.
