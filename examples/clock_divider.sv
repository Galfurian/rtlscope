// A divide-by-four clock: the second flop is clocked by the first one's output.

module toggle (
    input  logic clk,
    input  logic rst_n,
    output logic q
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) q <= 1'b0;
    else q <= ~q;
  end
endmodule

module clock_divider (
    input  logic clk,
    input  logic rst_n,
    output logic clk_div2,
    output logic clk_div4
);
  toggle u_div2 (.clk(clk),      .rst_n(rst_n), .q(clk_div2));
  toggle u_div4 (.clk(clk_div2), .rst_n(rst_n), .q(clk_div4));
endmodule
