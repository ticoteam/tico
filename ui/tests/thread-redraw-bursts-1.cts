// Seeds 5-9 of the random bursts in thread-redraw.cts, as their own script so each stays well under the script time limit.
process.env.TICO_REDRAW_SEEDS = '5-9';
require('./thread-redraw.cts');
