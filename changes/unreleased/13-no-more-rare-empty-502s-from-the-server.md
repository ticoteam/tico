### Fixed
- **No more rare empty 502s from the server.** About once in a few hundred API requests, a request reused a connection
  to the backend just as the backend closed it for being idle, and the app or a bot got a bare 502. The server's front
  door now drops idle connections before the backend does.
