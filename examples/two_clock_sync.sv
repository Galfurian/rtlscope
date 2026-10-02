// A two-flop synchronizer between two clock domains.

module sync_flop (
    input  logic clk,
    input  logic d,
    output logic q
);
  always_ff @(posedge clk) q <= d;
endmodule

module two_clock_sync (
    input  logic clk_a,
    input  logic clk_b,
    input  logic data_a,
    output logic data_b
);
  logic launched, meta;

  sync_flop u_launch (.clk(clk_a), .d(data_a),   .q(launched));
  sync_flop u_meta   (.clk(clk_b), .d(launched), .q(meta));
  sync_flop u_stable (.clk(clk_b), .d(meta),     .q(data_b));
endmodule
