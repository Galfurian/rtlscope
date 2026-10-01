// A counter: a register fed back through an incrementer.

module register #(
    parameter int W = 4
) (
    input  logic         clk,
    input  logic         rst,
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
  always_ff @(posedge clk) begin
    if (rst) q <= '0;
    else q <= d;
  end
endmodule

module incrementer #(
    parameter int W = 4
) (
    input  logic [W-1:0] a,
    output logic [W-1:0] y
);
  assign y = a + 1'b1;
endmodule

module counter (
    input  logic       clk,
    input  logic       rst,
    output logic [3:0] count
);
  logic [3:0] next;

  register    u_reg (.clk(clk), .rst(rst), .d(next), .q(count));
  incrementer u_inc (.a(count), .y(next));
endmodule
