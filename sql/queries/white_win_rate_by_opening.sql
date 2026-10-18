SELECT o.eco, o.name,
       COUNT(*) AS games,
       ROUND(AVG(g.result = '1-0'), 3) AS white_win_rate
FROM game g
JOIN opening o ON o.opening_key = g.opening_key
GROUP BY o.opening_key
HAVING COUNT(*) >= 30
ORDER BY white_win_rate DESC;