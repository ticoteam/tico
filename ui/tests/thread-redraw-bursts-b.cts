// Seeds 15-24 of the random bursts in thread-redraw.cts, as their own script so each stays under the script time limit.
process.env.TICO_REDRAW_SEEDS = '15-24';
require('./thread-redraw.cts');
