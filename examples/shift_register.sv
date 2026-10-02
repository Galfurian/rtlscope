// Three flip-flops in a chain, with an asynchronous active-low reset.

module dff (
    input  logic clk,
    input  logic rst_n,
    input  logic d,
    output logic q
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) q <= 1'b0;
    else q <= d;
  end
endmodule

module shift_register (
    input  logic clk,
    input  logic rst_n,
    input  logic din,
    output logic dout
);
  logic s1, s2;

  dff u0 (.clk(clk), .rst_n(rst_n), .d(din), .q(s1));
  dff u1 (.clk(clk), .rst_n(rst_n), .d(s1),  .q(s2));
  dff u2 (.clk(clk), .rst_n(rst_n), .d(s2),  .q(dout));
endmodule
