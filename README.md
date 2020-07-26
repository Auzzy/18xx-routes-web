# 18xx Route Finder

A webapp for finding the optimal set of routes for each railroad in 18xx games. The intention is for users to reflect the state of their game in the app, then run it to confirm they're running the best routes. If you're not familiar with 18xx, the [Wikipedia page](https://boardgamegeek.com/wiki/page/18xx) is honestly a good primer.

The app is hosted on Heroku: [https://routes18xx.herokuapp.com/](https://routes18xx.herokuapp.com/)

[![Screenshot of using 18xx Route Finder for 1846](https://user-images.githubusercontent.com/332270/88489048-d3c57f80-cf5f-11ea-9b47-ee548b195b0f.png)](https://routes18xx.herokuapp.com/)

This is the successor to my 1846 Route Finder (https://github.com/Auzzy/1846-routes-web). The backend was rewritten to support any 18xx game, instead of just 1846. Each game is defined by configuration files and optional hooks, making it fairly easy to add new titles. I'll probably need to add more features as I support more games, but Keith Thomasson's wonderful [Rules Difference List](http://www.fwtwr.com/18xx/rules_difference_list/single_list.htm) helped me plan ahead, so future extensions should be fairly straight-forward.

Using it is pretty simple.

1. Select your game
1. Click on board spaces to select a tile and orientation. Only legal selections are displayed.
1. Add railroads which have been floated, and indicate their trains and placed stations
1. Indicate who owns which privates

    * Only privates whose ownership directly impacts route value are implemented

1. Select a railroad from the Calculate menu, and once it's done calculating, the best route for each train will be displayed both in text and on the board.

    * Note it does some board validation ahead of time, but it leans towards permissiveness to allow you flexibility in how you enter your game. Once you click Calculate, it performs a full game validation, and will display any problems it finds.

[![Screenshot of using 18xx Route Finder for 1889](https://user-images.githubusercontent.com/332270/88489154-a0cfbb80-cf60-11ea-89ad-97c12c10e2dc.png)](https://routes18xx.herokuapp.com/)

The game state you enter is saved in your browser, so you can easily come back to it later if you need to (e.g. playing asynchronously).

If you have any issues, you can either file a bug on the Issues page, or click the "Report Issue" button in the app. That button collects the game data you've entered and emails it to me directly.

The list of features in my queue and their status is also tracked on the Issues page. And feel free to add your own feature requests.
